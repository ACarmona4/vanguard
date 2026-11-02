"""Read provider metrics with existing encrypted connections; no cloud writes."""

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import boto3
from botocore.config import Config
from google.auth.transport.requests import AuthorizedSession

from ..connections.credentials import gcp_credentials
from .catalog import AWS, GCP, metrics_for


def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def metadata_samples(resources):
    """Metadata gauges retain their inventory timestamp; absent values stay absent."""
    samples = []
    for resource in resources:
        observed = resource.get("last_seen_at")
        if isinstance(observed, str):
            observed = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        if observed is None:
            continue
        for metric in metrics_for(resource):
            if metric.attribute:
                value = _finite((resource.get("attributes") or {}).get(metric.attribute))
                if value is not None:
                    samples.append((resource, metric, observed.timestamp(), value))
    return samples


def aws_samples(resources, credentials, now):
    now = datetime.fromtimestamp(int(now.timestamp()) // 300 * 300, timezone.utc)
    session = boto3.Session(
        aws_access_key_id=credentials["access_key_id"],
        aws_secret_access_key=credentials["secret_access_key"],
        aws_session_token=credentials.get("session_token") or None,
    )
    regional = defaultdict(list)
    for resource in resources:
        entry = AWS.get(resource["resource_type"])
        if not entry:
            continue
        namespace, dimension, _ = entry
        identifier = resource["resource_id"] if dimension in {"InstanceId", "VolumeId"} else resource["name"]
        for metric in metrics_for(resource):
            if metric.cloud_name:
                regional[(resource["region"], metric.period)].append((resource, metric, {
                    "Metric": {"Namespace": namespace, "MetricName": metric.cloud_name,
                               "Dimensions": [{"Name": dimension, "Value": identifier}]
                               + [{"Name": name, "Value": value} for name, value in metric.dimensions]},
                    "Period": metric.period, "Stat": metric.statistic,
                }))
    samples, errors = metadata_samples(resources), []
    for (region, period), definitions in regional.items():
        client = session.client("cloudwatch", region_name=region,
                                config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}))
        for start in range(0, len(definitions), 500):
            batch = definitions[start:start + 500]
            queries = [{"Id": f"m{i}", "MetricStat": spec, "ReturnData": True}
                       for i, (_, _, spec) in enumerate(batch)]
            observations = {}
            try:
                pages = client.get_paginator("get_metric_data").paginate(
                    MetricDataQueries=queries, StartTime=now - timedelta(seconds=max(1200, period * 2)),
                    EndTime=now, ScanBy="TimestampDescending",
                )
                for page in pages:
                    for result in page.get("MetricDataResults", []):
                        if result.get("StatusCode") not in {"Complete", "PartialData"}:
                            errors.append(f"AWS {region}: metric unavailable")
                            continue
                        if result.get("StatusCode") == "PartialData":
                            errors.append(f"AWS {region}: partial data")
                        for timestamp, value in zip(result.get("Timestamps", []), result.get("Values", [])):
                            number = _finite(value)
                            if number is not None and timestamp <= now:
                                observations[(result["Id"], timestamp.timestamp())] = number
                # Export all returned buckets: repeated polls keep the original timestamp.
                for (key, timestamp), value in sorted(observations.items(), key=lambda item: item[0][1]):
                    resource, metric, _ = batch[int(key[1:])]
                    samples.append((resource, metric, timestamp, value * metric.scale))
            except Exception as exc:
                errors.append(f"AWS {region}: {type(exc).__name__}; check cloudwatch:GetMetricData")
    samples.extend(_rds_storage(samples))
    return samples, errors


def _rds_storage(samples):
    derived = []
    for resource, metric, timestamp, value in samples:
        if resource["resource_type"] != "rds_instance" or metric.key != "free_storage_bytes":
            continue
        size = _finite((resource.get("attributes") or {}).get("allocated_storage_gb"))
        capacity = (size or 0) * 1024**3
        definition = next((m for m in metrics_for(resource) if m.key == "filesystem_percent"), None)
        if definition and capacity > 0 and value <= capacity:
            derived.append((resource, definition, timestamp, (capacity - value) / capacity * 100))
    return derived


def _gcp_spec(resource):
    attributes = resource.get("attributes") or {}
    if resource["resource_type"] == "cloud_function" and attributes.get("generation") == "GEN_2":
        return GCP["cloud_run_service"][:2]
    return GCP[resource["resource_type"]][:2]


def _gcp_identifier(resource):
    if resource["resource_type"] == "cloudsql_instance":
        return f'{resource["scope_id"]}:{resource["name"]}'
    if resource["resource_type"] in {"cloud_run_service", "cloud_function"}:
        attributes = resource.get("attributes") or {}
        name = attributes.get("run_service") if attributes.get("generation") == "GEN_2" else resource["name"]
        return resource.get("region") or "", name
    return resource["resource_id"].rsplit("/", 1)[-1]


def gcp_samples(resources, credentials, now):
    now = datetime.fromtimestamp(int(now.timestamp()) // 300 * 300, timezone.utc)
    samples, errors = metadata_samples(resources), []
    grouped = defaultdict(list)
    for resource in resources:
        if resource["resource_type"] not in GCP:
            continue
        monitored_type, id_label = _gcp_spec(resource)
        for metric in metrics_for(resource):
            if metric.cloud_name:
                grouped[(resource["scope_id"], monitored_type, id_label, metric)].append(resource)
    with AuthorizedSession(gcp_credentials(credentials)) as session:
        for (project, monitored_type, id_label, metric), matching in grouped.items():
            indexed = defaultdict(list)
            for resource in matching:
                indexed[_gcp_identifier(resource)].append(resource)
            regional = monitored_type in {"cloud_run_revision", "cloud_function"}
            aligner = {"rate": "ALIGN_RATE", "max": "ALIGN_MAX", "p95": "ALIGN_PERCENTILE_95"}.get(metric.statistic, "ALIGN_MEAN")
            params = {
                "filter": f'metric.type = "{metric.cloud_name}" AND resource.type = "{monitored_type}"'
                          + (" AND " + metric.filter if metric.filter else ""),
                "interval.startTime": (now - timedelta(seconds=max(1200, metric.freshness_seconds))).isoformat(),
                "interval.endTime": now.isoformat(),
                "aggregation.alignmentPeriod": "300s",
                "aggregation.perSeriesAligner": aligner,
                "aggregation.crossSeriesReducer": "REDUCE_MAX" if metric.statistic == "max" or metric.reducer == "max" else "REDUCE_SUM",
                "aggregation.groupByFields": [f"resource.label.{id_label}", "resource.label.project_id"]
                                            + (["resource.label.location" if regional else "resource.label.zone"]
                                               if regional or monitored_type == "gce_instance" else []),
                "pageSize": 1000,
            }
            try:
                while True:
                    response = session.get(
                        f"https://monitoring.googleapis.com/v3/projects/{quote(project, safe='')}/timeSeries",
                        params=params, timeout=20,
                    )
                    response.raise_for_status()
                    body = response.json()
                    for series in body.get("timeSeries", []):
                        labels = series["resource"]["labels"]
                        key = (labels.get("location") or labels.get("region"), labels.get(id_label)) if regional else labels.get(id_label)
                        if labels.get("project_id") != project:
                            continue
                        for point in sorted(series.get("points", []), key=lambda p: p["interval"]["endTime"]):
                            value = point["value"]
                            number = _finite(value.get("doubleValue", value.get("int64Value")))
                            if number is None:
                                continue
                            timestamp = datetime.fromisoformat(point["interval"]["endTime"].replace("Z", "+00:00"))
                            if timestamp > now:
                                continue
                            for resource in indexed.get(key, []):
                                samples.append((resource, metric, timestamp.timestamp(), number * metric.scale))
                    token = body.get("nextPageToken")
                    if not token:
                        break
                    params["pageToken"] = token
            except Exception as exc:
                errors.append(f"GCP {monitored_type}/{metric.key}: {type(exc).__name__}; check Monitoring API and monitoring.timeSeries.list")
    return samples, errors
