"""Read provider metrics with existing encrypted connections; no cloud writes."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import boto3
from botocore.config import Config
from google.auth.transport.requests import AuthorizedSession

from ..cloud_credentials import gcp_credentials
from .catalog import AWS, GCP, metrics_for


def aws_samples(resources, credentials, now):
    # Exclude unfinished buckets: changing a value at the same timestamp is invalid in Prometheus.
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
        namespace, dimension, metrics = entry
        identifier = resource["resource_id"] if dimension in {"InstanceId", "VolumeId"} else resource["name"]
        for metric in metrics:
            if metric.cloud_name:
                regional[resource["region"]].append((resource, metric, {
                    "Metric": {"Namespace": metric.namespace or namespace, "MetricName": metric.cloud_name,
                               "Dimensions": [{"Name": dimension, "Value": identifier}]},
                    "Period": 300, "Stat": metric.statistic,
                }))
    samples, errors = [], []
    for region, definitions in regional.items():
        client = session.client("cloudwatch", region_name=region,
                                config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}))
        for start in range(0, len(definitions), 500):
            batch = definitions[start:start + 500]
            queries = [{"Id": f"m{i}", "MetricStat": spec, "ReturnData": True}
                       for i, (_, _, spec) in enumerate(batch)]
            latest = {}
            try:
                pages = client.get_paginator("get_metric_data").paginate(
                    MetricDataQueries=queries, StartTime=now - timedelta(minutes=20),
                    EndTime=now, ScanBy="TimestampDescending",
                )
                for page in pages:
                    for result in page.get("MetricDataResults", []):
                        if result.get("StatusCode") not in {"Complete", "PartialData"}:
                            errors.append(f"AWS {region}: métrica no disponible")
                            continue
                        if result.get("StatusCode") == "PartialData":
                            errors.append(f"AWS {region}: datos parciales")
                        for timestamp, value in zip(result.get("Timestamps", []), result.get("Values", [])):
                            previous = latest.get(result["Id"])
                            if previous is None or timestamp > previous[0]:
                                latest[result["Id"]] = (timestamp, value)
                for key, (timestamp, value) in latest.items():
                    resource, metric, _ = batch[int(key[1:])]
                    samples.append((resource, metric, timestamp.timestamp(), value * metric.scale))
            except Exception as exc:
                errors.append(f"AWS {region}: {type(exc).__name__}; revisa cloudwatch:GetMetricData")
    return samples, errors


def gcp_samples(resources, credentials, now):
    now = datetime.fromtimestamp(int(now.timestamp()) // 300 * 300, timezone.utc)
    samples, errors = [], []
    with AuthorizedSession(gcp_credentials(credentials)) as session:
        for resource_type, (monitored_type, id_label, metrics) in GCP.items():
            matching = [r for r in resources if r["resource_type"] == resource_type]
            if not matching:
                continue
            project = matching[0]["scope_id"]
            indexed = {_gcp_identifier(r): r for r in matching}
            for metric in metrics:
                if metric.agent:
                    continue
                params = {
                    "filter": f'metric.type = "{metric.cloud_name}" AND resource.type = "{monitored_type}"',
                    "interval.startTime": (now - timedelta(minutes=20)).isoformat(),
                    "interval.endTime": now.isoformat(),
                    "aggregation.alignmentPeriod": "300s",
                    "aggregation.perSeriesAligner": "ALIGN_RATE" if metric.statistic == "rate" else "ALIGN_MAX" if metric.statistic == "max" else "ALIGN_MEAN",
                    "aggregation.crossSeriesReducer": "REDUCE_MAX" if metric.statistic == "max" else "REDUCE_SUM",
                    "aggregation.groupByFields": [f"resource.label.{id_label}", "resource.label.project_id"],
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
                            resource = indexed.get(labels.get(id_label))
                            if not resource or labels.get("project_id") != project or metric not in metrics_for(resource):
                                continue
                            points = series.get("points", [])
                            if not points:
                                continue
                            point = max(points, key=lambda p: p["interval"]["endTime"])
                            value = point["value"]
                            number = value.get("doubleValue", value.get("int64Value"))
                            if number is not None:
                                timestamp = datetime.fromisoformat(point["interval"]["endTime"].replace("Z", "+00:00"))
                                samples.append((resource, metric, timestamp.timestamp(), float(number) * metric.scale))
                        token = body.get("nextPageToken")
                        if not token:
                            break
                        params["pageToken"] = token
                except Exception as exc:
                    errors.append(f"GCP {resource_type}/{metric.key}: {type(exc).__name__}; revisa Monitoring API y monitoring.timeSeries.list")
    return samples, errors


def _gcp_identifier(resource):
    if resource["resource_type"] == "cloudsql_instance":
        return f'{resource["scope_id"]}:{resource["name"]}'
    return resource["resource_id"].rsplit("/", 1)[-1]
