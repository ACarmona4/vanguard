"""Read-only adapters for AWS Cost Explorer and GCP Billing export."""

from __future__ import annotations

import re
import time
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from google.auth.transport.requests import AuthorizedSession

from ..accounts.credentials import gcp_credentials
from .catalog import category_for

GCP_TABLE_PATTERN = re.compile(
    r"^(?P<project>[a-z][a-z0-9-]{4,62}[a-z0-9])\."
    r"(?P<dataset>[A-Za-z0-9_]+)\.(?P<table>[A-Za-z0-9_]+)$"
)
AWS_COST_METRIC = "NetUnblendedCost"


def validate_gcp_table(value: str) -> str:
    table = value.strip().strip("`")
    if not GCP_TABLE_PATTERN.fullmatch(table):
        raise ValueError("Use project.dataset.table for the BigQuery billing export")
    return table


def _base_record(source, day, service, amount, currency, *, level="service", **extra):
    return {
        "owner_id": source["owner_id"],
        "connection_id": source["connection_id"],
        "usage_date": day,
        "provider": source["provider"],
        "scope_id": source["scope_id"],
        "level": level,
        "service": service or "Other",
        "category": category_for(service or "Other"),
        "resource_id": extra.get("resource_id"),
        "resource_name": extra.get("resource_name"),
        "region": extra.get("region"),
        "currency": currency or "USD",
        "amount": Decimal(str(amount)),
        "estimated": bool(extra.get("estimated")),
    }


def collect_aws(source: dict, credentials: dict, start: date, end: date):
    session = boto3.Session(
        aws_access_key_id=credentials["access_key_id"],
        aws_secret_access_key=credentials["secret_access_key"],
        aws_session_token=credentials.get("session_token") or None,
    )
    client = session.client(
        "ce", region_name="us-east-1",
        config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 3}),
    )
    records = []
    query_end = min(end, date.today())
    if start >= query_end:
        return records, False
    request = {
        "TimePeriod": {"Start": start.isoformat(), "End": query_end.isoformat()},
        "Granularity": "DAILY",
        "Metrics": [AWS_COST_METRIC],
        "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
    }
    while True:
        response = client.get_cost_and_usage(**request)
        for period in response.get("ResultsByTime", []):
            day = date.fromisoformat(period["TimePeriod"]["Start"])
            for group in period.get("Groups", []):
                metric = group["Metrics"][AWS_COST_METRIC]
                records.append(_base_record(
                    source, day, group["Keys"][0], metric["Amount"], metric["Unit"],
                    estimated=period.get("Estimated", False),
                ))
        token = response.get("NextPageToken")
        if not token:
            break
        request["NextPageToken"] = token

    detail_available = False
    detail_start = max(start, date.today() - timedelta(days=13))
    detail_request = {
        "TimePeriod": {"Start": detail_start.isoformat(), "End": query_end.isoformat()},
        "Granularity": "DAILY",
        "Metrics": [AWS_COST_METRIC],
        "Filter": {"Dimensions": {
            "Key": "SERVICE", "Values": ["Amazon Elastic Compute Cloud - Compute"]
        }},
        "GroupBy": [{"Type": "DIMENSION", "Key": "RESOURCE_ID"}],
    }
    try:
        while True:
            response = client.get_cost_and_usage_with_resources(**detail_request)
            detail_available = True
            for period in response.get("ResultsByTime", []):
                day = date.fromisoformat(period["TimePeriod"]["Start"])
                for group in period.get("Groups", []):
                    resource_id = group["Keys"][0]
                    if not resource_id:
                        continue
                    metric = group["Metrics"][AWS_COST_METRIC]
                    records.append(_base_record(
                        source, day, "Amazon Elastic Compute Cloud - Compute",
                        metric["Amount"], metric["Unit"], level="resource",
                        resource_id=resource_id, estimated=period.get("Estimated", False),
                    ))
            token = response.get("NextPageToken")
            if not token:
                break
            detail_request["NextPageToken"] = token
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {
            "DataUnavailableException", "ValidationException", "AccessDeniedException"
        }:
            raise
        detail_available = False
    return records, detail_available


def _query_parameters(start: date, end: date, project_id: str):
    return [
        {"name": "start_date", "parameterType": {"type": "DATE"},
         "parameterValue": {"value": start.isoformat()}},
        {"name": "end_date", "parameterType": {"type": "DATE"},
         "parameterValue": {"value": end.isoformat()}},
        {"name": "project_id", "parameterType": {"type": "STRING"},
         "parameterValue": {"value": project_id}},
    ]


def _bigquery_rows(session, query_project, location, query, parameters):
    response = session.post(
        f"https://bigquery.googleapis.com/bigquery/v2/projects/{quote(query_project, safe='')}/queries",
        json={
            "query": query,
            "useLegacySql": False,
            "parameterMode": "NAMED",
            "queryParameters": parameters,
            "location": location,
            "timeoutMs": 60000,
            "maxResults": 10000,
            "maximumBytesBilled": "10000000000",
            "useQueryCache": True,
        },
        timeout=70,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise RuntimeError(body["errors"][0].get("message", "BigQuery query failed"))
    job = body.get("jobReference", {})
    while not body.get("jobComplete", True):
        time.sleep(2)
        response = session.get(
            f"https://bigquery.googleapis.com/bigquery/v2/projects/{quote(query_project, safe='')}"
            f"/queries/{quote(job['jobId'], safe='')}",
            params={"location": location, "timeoutMs": 60000, "maxResults": 10000},
            timeout=70,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            raise RuntimeError(body["errors"][0].get("message", "BigQuery query failed"))
    fields = [field["name"] for field in body.get("schema", {}).get("fields", [])]
    while True:
        for row in body.get("rows", []):
            yield {name: cell.get("v") for name, cell in zip(fields, row.get("f", []))}
        token = body.get("pageToken")
        if not token:
            break
        response = session.get(
            f"https://bigquery.googleapis.com/bigquery/v2/projects/{quote(query_project, safe='')}"
            f"/queries/{quote(job['jobId'], safe='')}",
            params={"location": location, "pageToken": token, "maxResults": 10000},
            timeout=70,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            raise RuntimeError(body["errors"][0].get("message", "BigQuery query failed"))


def collect_gcp(source: dict, credentials: dict, start: date, end: date):
    table = validate_gcp_table(source["billing_export_table"])
    location = source.get("billing_location") or "US"
    common_where = """
      project.id = @project_id
      AND DATE(usage_start_time) >= @start_date
      AND DATE(usage_start_time) < @end_date
      AND _PARTITIONTIME >= TIMESTAMP_SUB(TIMESTAMP(@start_date), INTERVAL 3 DAY)
      AND _PARTITIONTIME < TIMESTAMP_ADD(TIMESTAMP(@end_date), INTERVAL 3 DAY)
    """
    service_query = f"""
      SELECT DATE(usage_start_time) AS usage_date,
             service.description AS service, location.region AS region, currency,
             SUM(cost + IFNULL((SELECT SUM(credit.amount) FROM UNNEST(credits) credit), 0)) AS amount
      FROM `{table}`
      WHERE {common_where}
      GROUP BY usage_date, service, region, currency
      ORDER BY usage_date
    """
    parameters = _query_parameters(start, end, source["scope_id"])
    records = []
    with AuthorizedSession(gcp_credentials(credentials)) as session:
        for row in _bigquery_rows(session, source["scope_id"], location, service_query, parameters):
            records.append(_base_record(
                source, date.fromisoformat(row["usage_date"]), row["service"],
                row["amount"], row["currency"], region=row.get("region"),
            ))
        resource_query = f"""
          SELECT DATE(usage_start_time) AS usage_date,
                 service.description AS service, location.region AS region, currency,
                 resource.global_name AS resource_id, ANY_VALUE(resource.name) AS resource_name,
                 SUM(cost + IFNULL((SELECT SUM(credit.amount) FROM UNNEST(credits) credit), 0)) AS amount
          FROM `{table}`
          WHERE {common_where}
            AND resource.global_name IS NOT NULL AND resource.global_name != ''
          GROUP BY usage_date, service, region, currency, resource_id
          ORDER BY usage_date
        """
        try:
            for row in _bigquery_rows(session, source["scope_id"], location, resource_query, parameters):
                records.append(_base_record(
                    source, date.fromisoformat(row["usage_date"]), row["service"],
                    row["amount"], row["currency"], level="resource",
                    resource_id=row["resource_id"], resource_name=row.get("resource_name"),
                    region=row.get("region"),
                ))
            detail_available = True
        except Exception:
            detail_available = False
    return records, detail_available
