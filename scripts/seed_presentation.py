"""Seed the Northstar presentation workspace without modifying cloud workspaces.

Run from the project root: PYTHONPATH=backend/src .venv/bin/python scripts/seed_presentation.py
Re-running preserves recommendation decisions and tickets. Samples replay relative
 to the current clock; billing dates roll daily with the recommendation worker.
"""

import argparse
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid5

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from vanguard.connections import repository as connections
from vanguard.costs.catalog import category_for
from vanguard.inventory.database import UPSERT_SQL
from vanguard.presentation import repository as presentation
from vanguard.recommendations import repository as recommendations
from vanguard.recommendations import tickets
from vanguard.recommendations.worker import collect_workspace

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = str(
    uuid5(UUID("e5480c96-70c8-440a-bf38-3a649ad03c70"), "northstar-commerce")
)
SERVICES = {
    "ec2_instance": "Amazon Elastic Compute Cloud - Compute",
    "ebs_volume": "Amazon Elastic Block Store",
    "rds_instance": "Amazon Relational Database Service",
    "rds_cluster": "Amazon Relational Database Service",
    "rds_snapshot": "Amazon Relational Database Service",
    "s3_bucket": "Amazon Simple Storage Service",
    "lambda_function": "AWS Lambda",
    "dynamodb_table": "Amazon DynamoDB",
    "sqs_queue": "Amazon Simple Queue Service",
    "sns_topic": "Amazon Simple Notification Service",
    "compute_instance": "Compute Engine",
    "persistent_disk": "Compute Engine",
    "ip_address": "Networking",
    "cloudsql_instance": "Cloud SQL",
    "cloud_run_service": "Cloud Run",
    "cloud_function": "Cloud Functions",
    "storage_bucket": "Cloud Storage",
    "bigquery_dataset": "BigQuery",
    "bigquery_table": "BigQuery",
    "pubsub_topic": "Cloud Pub/Sub",
    "pubsub_subscription": "Cloud Pub/Sub",
}


def scenarios(now):
    old = (now - timedelta(days=46)).isoformat()
    # provider, resource type, name, daily USD cost, attributes, latest base readings
    return [
        (
            "aws",
            "ec2_instance",
            "checkout-api-01",
            8.4,
            {"instance_type": "m6i.xlarge"},
            {"cpu_percent": 94, "memory_percent": 87, "filesystem_percent": 64},
        ),
        (
            "aws",
            "ec2_instance",
            "catalog-worker-01",
            5.6,
            {"instance_type": "m6i.xlarge"},
            {"cpu_percent": 7, "memory_percent": 24, "filesystem_percent": 32},
        ),
        (
            "aws",
            "ec2_instance",
            "edge-gateway-01",
            3.2,
            {"instance_type": "t3.large"},
            {"cpu_percent": 42, "memory_percent": 48, "filesystem_percent": 35},
        ),
        (
            "aws",
            "ec2_instance",
            "reporting-archive-01",
            0,
            {"instance_type": "t3.large", "stopped": True},
            {},
        ),
        (
            "aws",
            "ebs_volume",
            "reporting-archive-data",
            0.53,
            {"size_gb": 200, "attached": True},
            {},
        ),
        ("aws", "ebs_volume", "legacy-export-data", 0.8, {"size_gb": 300}, {}),
        (
            "aws",
            "rds_instance",
            "orders-primary",
            16.8,
            {
                "engine": "postgres",
                "allocated_storage_gb": 500,
                "backup_retention_days": 7,
            },
            {
                "cpu_percent": 86,
                "filesystem_percent": 67,
                "freeable_memory_bytes": 3 * 1024**3,
                "free_storage_bytes": 165 * 1024**3,
                "connections": 184,
            },
        ),
        (
            "aws",
            "rds_instance",
            "catalog-staging",
            4.7,
            {
                "engine": "postgres",
                "allocated_storage_gb": 100,
                "backup_retention_days": 0,
            },
            {
                "cpu_percent": 8,
                "filesystem_percent": 32,
                "freeable_memory_bytes": 5 * 1024**3,
                "connections": 8,
            },
        ),
        (
            "aws",
            "rds_cluster",
            "sessions-aurora",
            9.1,
            {"engine": "aurora-postgresql", "backup_retention_days": 7},
            {
                "capacity_percent": 93,
                "capacity_units": 15,
                "storage_bytes": 140 * 1024**3,
            },
        ),
        (
            "aws",
            "rds_snapshot",
            "orders-monthly-backup",
            1.2,
            {"created_at": old, "allocated_storage_gb": 500},
            {},
        ),
        (
            "aws",
            "lambda_function",
            "payment-webhook",
            1.9,
            {"timeout_seconds": 10, "memory_mb": 1024, "provisioned_concurrency": 0},
            {
                "invocations_per_second": 18,
                "errors_per_second": 0.6,
                "throttles_per_second": 0.2,
                "duration_p95_ms": 8700,
                "duration_ms": 4200,
                "concurrent_executions": 62,
            },
        ),
        (
            "aws",
            "lambda_function",
            "image-preview-legacy",
            2.1,
            {"timeout_seconds": 30, "memory_mb": 2048, "provisioned_concurrency": 3},
            {
                "invocations_per_second": 0,
                "errors_per_second": 0,
                "throttles_per_second": 0,
                "duration_p95_ms": 0,
                "duration_ms": 0,
                "concurrent_executions": 0,
            },
        ),
        (
            "aws",
            "lambda_function",
            "shipment-notifier",
            0.45,
            {"timeout_seconds": 30, "memory_mb": 512, "provisioned_concurrency": 0},
            {
                "invocations_per_second": 6,
                "errors_per_second": 0,
                "throttles_per_second": 0,
                "duration_p95_ms": 340,
                "duration_ms": 180,
                "concurrent_executions": 3,
            },
        ),
        (
            "aws",
            "dynamodb_table",
            "shopping-sessions",
            5.2,
            {"billing_mode": "PROVISIONED"},
            {
                "read_units_per_second": 6,
                "write_units_per_second": 2,
                "provisioned_read_units": 100,
                "provisioned_write_units": 50,
                "read_throttles_per_second": 0,
                "write_throttles_per_second": 0,
            },
        ),
        (
            "aws",
            "dynamodb_table",
            "inventory-reservations",
            2.2,
            {"billing_mode": "PAY_PER_REQUEST"},
            {
                "read_units_per_second": 74,
                "write_units_per_second": 38,
                "read_throttles_per_second": 0,
                "write_throttles_per_second": 0.4,
            },
        ),
        (
            "aws",
            "sqs_queue",
            "order-processing",
            0.38,
            {"long_polling_seconds": 0},
            {
                "oldest_message_seconds": 1200,
                "queued_messages": 2840,
                "messages_sent_per_second": 20,
                "messages_received_per_second": 12,
                "empty_receives_per_second": 4,
            },
        ),
        (
            "aws",
            "sqs_queue",
            "shipment-events",
            0.21,
            {"long_polling_seconds": 20},
            {
                "oldest_message_seconds": 12,
                "queued_messages": 24,
                "messages_sent_per_second": 8,
                "messages_received_per_second": 8,
                "empty_receives_per_second": 0,
            },
        ),
        (
            "aws",
            "sns_topic",
            "customer-notifications",
            0.27,
            {"subscriptions": 3},
            {"published_per_second": 12, "failed_per_second": 0.25},
        ),
        (
            "aws",
            "s3_bucket",
            "northstar-order-exports",
            1.85,
            {"lifecycle_rules": 0, "intelligent_tiering": False},
            {"storage_bytes": 1800 * 1024**3, "objects": 480000},
        ),
        (
            "aws",
            "s3_bucket",
            "northstar-static-assets",
            0.67,
            {"lifecycle_rules": 2, "intelligent_tiering": True},
            {"storage_bytes": 420 * 1024**3, "objects": 125000},
        ),
        (
            "gcp",
            "compute_instance",
            "recommendation-api-01",
            6.8,
            {"machine_type": "e2-standard-4"},
            {"cpu_percent": 68, "memory_percent": 94, "filesystem_percent": 56},
        ),
        (
            "gcp",
            "compute_instance",
            "batch-export-worker",
            4.4,
            {"machine_type": "e2-standard-4"},
            {"cpu_percent": 9, "memory_percent": 29, "filesystem_percent": 28},
        ),
        (
            "gcp",
            "compute_instance",
            "search-indexer-01",
            7.1,
            {"machine_type": "n2-standard-4"},
            {"cpu_percent": 54, "memory_percent": 62, "filesystem_percent": 43},
        ),
        (
            "gcp",
            "persistent_disk",
            "retired-analytics-data",
            0.6,
            {"users": [], "size_gb": 200},
            {},
        ),
        (
            "gcp",
            "ip_address",
            "legacy-public-endpoint",
            0.12,
            {"users": [], "address_type": "EXTERNAL", "address": "34.72.18.42"},
            {},
        ),
        (
            "gcp",
            "cloudsql_instance",
            "analytics-primary",
            13.9,
            {
                "database_version": "POSTGRES_15",
                "backups_enabled": True,
                "data_disk_size_gb": 200,
            },
            {
                "cpu_percent": 57,
                "memory_percent": 76,
                "filesystem_percent": 88,
                "connections": 126,
            },
        ),
        (
            "gcp",
            "cloudsql_instance",
            "warehouse-staging",
            4.1,
            {
                "database_version": "POSTGRES_15",
                "backups_enabled": False,
                "data_disk_size_gb": 100,
            },
            {
                "cpu_percent": 6,
                "memory_percent": 22,
                "filesystem_percent": 12,
                "connections": 6,
            },
        ),
        (
            "gcp",
            "cloud_run_service",
            "product-recommendations",
            3.8,
            {"min_instances": 2},
            {
                "requests_per_second": 36,
                "cpu_percent": 91,
                "memory_percent": 86,
                "instances": 6,
                "billable_instance_seconds": 5.4,
                "duration_p95_ms": 1420,
                "errors_per_second": 0.8,
            },
        ),
        (
            "gcp",
            "cloud_run_service",
            "legacy-campaign-api",
            2.3,
            {"min_instances": 2},
            {
                "requests_per_second": 0,
                "cpu_percent": 0,
                "memory_percent": 18,
                "instances": 2,
                "billable_instance_seconds": 2,
                "duration_p95_ms": 0,
                "errors_per_second": 0,
            },
        ),
        (
            "gcp",
            "cloud_function",
            "invoice-processor",
            0.82,
            {"generation": "GEN_1", "min_instances": 0, "timeout_seconds": 60},
            {
                "invocations_per_second": 4,
                "duration_p95_ms": 52000,
                "errors_per_second": 0.16,
            },
        ),
        (
            "gcp",
            "cloud_function",
            "customer-event-enricher",
            0.48,
            {
                "generation": "GEN_2",
                "run_service": "customer-event-enricher",
                "min_instances": 0,
                "timeout_seconds": 60,
            },
            {
                "requests_per_second": 9,
                "cpu_percent": 32,
                "memory_percent": 41,
                "instances": 2,
                "billable_instance_seconds": 1.1,
                "duration_p95_ms": 220,
                "errors_per_second": 0,
            },
        ),
        (
            "gcp",
            "pubsub_subscription",
            "analytics-events-consumer",
            0.44,
            {},
            {
                "oldest_message_seconds": 640,
                "queued_messages": 1860,
                "acked_per_second": 18,
            },
        ),
        (
            "gcp",
            "pubsub_topic",
            "analytics-events",
            0.34,
            {"subscriptions": 2},
            {"retained_bytes": 180 * 1024**2, "published_per_second": 25},
        ),
        (
            "gcp",
            "pubsub_topic",
            "retired-campaign-events",
            0.04,
            {"subscriptions": 0},
            {"retained_bytes": 0, "published_per_second": 0},
        ),
        (
            "gcp",
            "storage_bucket",
            "northstar-analytics-archive",
            1.4,
            {"lifecycle_rules": 0, "autoclass": False},
            {
                "storage_bytes": 1600 * 1024**3,
                "objects": 346000,
                "requests_per_second": 3,
            },
        ),
        (
            "gcp",
            "bigquery_dataset",
            "commerce_analytics",
            0.15,
            {},
            {"storage_bytes": 640 * 1024**3, "tables": 18},
        ),
        (
            "gcp",
            "bigquery_table",
            "commerce_analytics.events_raw",
            4.6,
            {
                "table_type": "TABLE",
                "size_bytes": 420 * 1024**3,
                "rows": 780000000,
                "queries_7d": 146,
                "bytes_billed_7d": 18 * 1024**4,
                "partitioned": False,
                "expires_at": None,
                "created_at": old,
            },
            {},
        ),
        (
            "gcp",
            "bigquery_table",
            "commerce_analytics.daily_orders",
            1.2,
            {
                "table_type": "TABLE",
                "size_bytes": 120 * 1024**3,
                "rows": 46000000,
                "queries_7d": 84,
                "bytes_billed_7d": 2 * 1024**4,
                "partitioned": True,
                "require_partition_filter": False,
                "expires_at": None,
                "created_at": old,
            },
            {},
        ),
        (
            "gcp",
            "bigquery_table",
            "commerce_analytics.campaign_backup",
            0.3,
            {
                "table_type": "TABLE",
                "size_bytes": 24 * 1024**3,
                "rows": 2400000,
                "queries_7d": 0,
                "bytes_billed_7d": 0,
                "partitioned": False,
                "expires_at": None,
                "created_at": old,
            },
            {},
        ),
    ]


def resource_identifier(provider, kind, name, index):
    if provider == "aws":
        if kind == "ec2_instance":
            return f"i-0a7e9b2c{index:09x}"
        if kind == "ebs_volume":
            return f"vol-0d7e9b2c{index:09x}"
        if kind == "s3_bucket":
            return name
        if kind == "sqs_queue":
            return f"https://sqs.us-east-1.amazonaws.com/482716305924/{name}"
        return (
            f"arn:aws:{ {'lambda_function': 'lambda', 'dynamodb_table': 'dynamodb', 'sns_topic': 'sns'}.get(kind, 'rds') }:us-east-1:482716305924:"
            + {
                "lambda_function": "function:",
                "dynamodb_table": "table/",
                "sns_topic": "",
                "rds_instance": "db:",
                "rds_cluster": "cluster:",
                "rds_snapshot": "snapshot:",
            }[kind]
            + name
        )
    if kind == "pubsub_topic":
        return "projects/northstar-platform/topics/" + name
    if kind == "pubsub_subscription":
        return "projects/northstar-platform/subscriptions/" + name
    if kind == "cloud_function":
        return "us-central1/" + name
    return name


def billing_id(resource):
    if resource["provider"] == "aws":
        if resource["resource_type"] == "sqs_queue":
            return "arn:aws:sqs:us-east-1:482716305924:" + resource["name"]
        return resource["resource_id"]
    kind = resource["resource_type"]
    name = resource["name"]
    base = "projects/northstar-platform/"
    paths = {
        "compute_instance": f"//compute.googleapis.com/{base}zones/us-central1-a/instances/{name}",
        "persistent_disk": f"//compute.googleapis.com/{base}zones/us-central1-a/disks/{name}",
        "cloudsql_instance": f"//sqladmin.googleapis.com/{base}instances/{name}",
        "cloud_run_service": f"//run.googleapis.com/{base}locations/us-central1/services/{name}",
        "cloud_function": f"//cloudfunctions.googleapis.com/{base}locations/us-central1/functions/{name}",
        "storage_bucket": f"//storage.googleapis.com/{name}",
        "pubsub_topic": f"//pubsub.googleapis.com/{resource['resource_id']}",
        "pubsub_subscription": f"//pubsub.googleapis.com/{resource['resource_id']}",
    }
    if kind == "bigquery_table":
        dataset, table = name.split(".", 1)
        return f"//bigquery.googleapis.com/{base}datasets/{dataset}/tables/{table}"
    return paths.get(kind, name)


def samples(metric, base, index, name):
    values = []
    for i in range(8 * 288 + 1):
        age = (8 * 288 - i) * 300
        daily = (math.sin((i % 288) / 288 * math.tau - 1.3) + 1) / 2
        ripple = math.sin(i * 0.41 + index) * 0.025
        if base == 0:
            value = 0
        elif metric.startswith("provisioned_"):
            value = base
        elif metric in {"storage_bytes", "objects", "retained_bytes"}:
            value = base * (0.94 + 0.06 * i / (8 * 288))
        elif metric == "filesystem_percent" and name == "analytics-primary":
            value = base - age / 3600 * 0.65 if age <= 86400 else 58 + 5 * daily
        elif age <= 1800:
            value = base * (1 + ripple * 0.35)
        else:
            value = base * (0.48 + 0.5 * daily + ripple)
        # Keep known idle / low-capacity scenarios low, including hourly peaks.
        if metric.endswith("_percent"):
            value = min(99, max(0, value))
        values.append(round(max(0, value), 5))
    return values


def seed(db, workspace=WORKSPACE):
    now = datetime.now(timezone.utc)
    existing = db.execute(
        "SELECT id,full_name FROM app_users ORDER BY created_at LIMIT 1"
    ).fetchone()
    if not existing:
        raise RuntimeError(
            "Create an application account before seeding the presentation."
        )
    db.execute(
        "INSERT INTO organizations(id,name) VALUES (%s,'Northstar Commerce') ON CONFLICT(id) DO NOTHING",
        (workspace,),
    )
    db.execute(
        "INSERT INTO organization_members(organization_id,user_id,role) SELECT %s,user_id,role FROM organization_members WHERE organization_id=%s ON CONFLICT DO NOTHING",
        (workspace, existing["id"]),
    )
    db.execute(
        "INSERT INTO organization_members(organization_id,user_id,role) VALUES (%s,%s,'owner') ON CONFLICT DO NOTHING",
        (workspace, existing["id"]),
    )
    presentation.ensure_schema(db)
    db.execute(
        "INSERT INTO presentation_workspaces(owner_id) VALUES (%s) ON CONFLICT DO NOTHING",
        (workspace,),
    )
    presentation.register(workspace)
    db.commit()
    configured = {}
    for provider, scope, label in [
        ("aws", "482716305924", "AWS Production"),
        ("gcp", "northstar-platform", "GCP Data Platform"),
    ]:
        row = db.execute(
            "SELECT id FROM cloud_connections WHERE owner_id=%s AND provider=%s AND scope_id=%s",
            (workspace, provider, scope),
        ).fetchone()
        if not row:
            row = connections.create_connection(
                db,
                owner_id=workspace,
                provider=provider,
                name=label,
                scope_id=scope,
                regions=["us-east-1" if provider == "aws" else "us-central1"],
                credential_hint="Managed telemetry",
                identity="Platform Operations",
                encrypted_credentials="offline-presentation",
            )
        configured[provider] = row["id"]
        db.execute(
            "UPDATE cloud_connections SET enabled=false,status='success',last_synced_at=now() WHERE id=%s",
            (row["id"],),
        )
        db.execute(
            "INSERT INTO cost_sources(connection_id,owner_id,provider,enabled,status,detail_available,last_synced_at) VALUES(%s,%s,%s,false,'success',true,now()) ON CONFLICT(connection_id) DO UPDATE SET enabled=false,status='success',detail_available=true,last_synced_at=now()",
            (row["id"], workspace, provider),
        )
    db.execute(
        "DELETE FROM cost_records WHERE owner_id=%s AND connection_id=ANY(%s)",
        (workspace, list(configured.values())),
    )
    count = 0
    from vanguard.utilization.catalog import metrics_for

    for index, (provider, kind, name, cost, attrs, readings) in enumerate(
        scenarios(now)
    ):
        attrs = {
            **attrs,
            "telemetry_profile": "commerce-production",
            "labels": {
                "environment": "production",
                "team": "platform" if provider == "aws" else "data",
            },
        }
        rid = resource_identifier(provider, kind, name, index)
        raw = {}
        if kind == "ec2_instance":
            raw = {"InstanceType": attrs["instance_type"], "BlockDeviceMappings": []}
        if kind == "ebs_volume":
            raw = {
                "Size": attrs["size_gb"],
                "Attachments": [
                    {"InstanceId": resource_identifier("aws", "ec2_instance", "", 3)}
                ]
                if attrs.get("attached")
                else [],
            }
        if kind == "ec2_instance" and attrs.get("stopped"):
            raw["BlockDeviceMappings"] = [
                {"Ebs": {"VolumeId": resource_identifier("aws", "ebs_volume", "", 4)}}
            ]
        if kind == "cloudsql_instance":
            raw = {"settings": {"dataDiskSizeGb": str(attrs["data_disk_size_gb"])}}
        status = (
            "stopped"
            if attrs.get("stopped")
            else "available"
            if kind == "ebs_volume" and not attrs.get("attached")
            else "RESERVED"
            if kind == "ip_address"
            else "READY"
            if kind == "persistent_disk"
            else "RUNNABLE"
            if kind == "cloudsql_instance"
            else "running"
            if kind == "ec2_instance"
            else "ACTIVE"
        )
        resource = {
            "owner_id": workspace,
            "connection_id": configured[provider],
            "provider": provider,
            "resource_type": kind,
            "resource_id": rid,
            "name": name,
            "scope_id": "482716305924" if provider == "aws" else "northstar-platform",
            "region": "us-east-1" if provider == "aws" else "us-central1",
            "zone": None if provider == "aws" else "us-central1-a",
            "status": status,
            "attributes": Jsonb(attrs),
            "raw_data": Jsonb(raw),
            "collected_at": now,
        }
        db.execute(UPSERT_SQL, resource)
        row = db.execute(
            "SELECT * FROM inventory_resources WHERE owner_id=%s AND provider=%s AND resource_type=%s AND resource_id=%s",
            (workspace, provider, kind, rid),
        ).fetchone()
        for definition in metrics_for(row):
            if definition.attribute:
                continue
            base = readings.get(
                definition.key,
                {
                    "network_in_bytes_per_second": 2.4 * 1024**2,
                    "network_out_bytes_per_second": 1.6 * 1024**2,
                    "disk_read_bytes_per_second": 640000,
                    "disk_write_bytes_per_second": 320000,
                    "disk_queue": 0.8,
                    "read_iops": 140,
                    "write_iops": 70,
                }.get(definition.key, 0),
            )
            if attrs.get("stopped"):
                continue
            db.execute(
                "INSERT INTO presentation_metrics(owner_id,inventory_id,metric,samples) VALUES(%s,%s,%s,%s) ON CONFLICT(owner_id,inventory_id,metric) DO UPDATE SET samples=excluded.samples",
                (
                    workspace,
                    row["id"],
                    definition.key,
                    Jsonb(samples(definition.key, base, index, name)),
                ),
            )
            count += 2305
        service = SERVICES[kind]
        with db.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO cost_records(owner_id,connection_id,usage_date,provider,scope_id,level,service,category,resource_id,resource_name,region,currency,amount) VALUES(%s,%s,%s,%s,%s,'resource',%s,%s,%s,%s,%s,'USD',%s)""",
                [
                    (
                        workspace,
                        configured[provider],
                        now.date() - timedelta(days=day),
                        provider,
                        row["scope_id"],
                        service,
                        category_for(service),
                        billing_id(row),
                        name,
                        row["region"],
                        round(
                            cost
                            * (
                                0.78
                                + 0.16 * math.sin(day * 0.31 + index)
                                + 0.28 * (60 - day) / 60
                            )
                            * (
                                0.86
                                if (now.date() - timedelta(days=day)).weekday() > 4
                                else 1
                            ),
                            5,
                        ),
                    )
                    for day in range(1, 61)
                ],
            )
    db.execute(
        """INSERT INTO cost_records(owner_id,connection_id,usage_date,provider,scope_id,level,service,category,region,currency,amount)
    SELECT owner_id,connection_id,usage_date,provider,scope_id,'service',service,category,max(region),currency,sum(amount)
    FROM cost_records WHERE owner_id=%s AND connection_id=ANY(%s) AND level='resource' GROUP BY owner_id,connection_id,usage_date,provider,scope_id,service,category,currency""",
        (workspace, list(configured.values())),
    )
    db.execute(
        "UPDATE presentation_workspaces SET billing_date=CURRENT_DATE WHERE owner_id=%s",
        (workspace,),
    )
    db.commit()
    collect_workspace(db, workspace)
    # Exercise the normal recommendation -> ticket transitions, once per finding.
    if not db.execute(
        "SELECT t.id FROM recommendation_tickets t JOIN recommendation_findings f ON f.id=t.finding_id JOIN inventory_resources r ON r.id=(f.finding->>'resource_id')::bigint WHERE t.organization_id=%s AND r.attributes->>'telemetry_profile'='commerce-production' LIMIT 1",
        (workspace,),
    ).fetchone():
        rows = db.execute(
            "SELECT id FROM recommendation_findings WHERE organization_id=%s AND finding->>'kind'='recommendation' AND (finding->>'resource_id')::bigint IN (SELECT id FROM inventory_resources WHERE owner_id=%s AND attributes->>'telemetry_profile'='commerce-production') ORDER BY first_detected_at,id LIMIT 6",
            (workspace, workspace),
        ).fetchall()
        for i, row in enumerate(rows):
            row = recommendations.get_finding(db, workspace, row["id"])
            if i < 2:
                ticket = tickets.ensure(db, row, existing)
                tickets.event(
                    db,
                    ticket,
                    "commented",
                    "Review scheduled with the workload owner.",
                    existing,
                )
            else:
                recommendations.change(
                    db,
                    workspace,
                    row["id"],
                    SimpleNamespace(
                        version=row["version"],
                        action="approve",
                        note="Approved after reviewing workload requirements.",
                    ),
                    existing,
                )
                ticket = db.execute(
                    "SELECT * FROM recommendation_tickets WHERE organization_id=%s AND finding_id=%s",
                    (workspace, row["id"]),
                ).fetchone()
                db.execute(
                    "UPDATE recommendation_tickets SET status='in_progress',due_date=CURRENT_DATE+2 WHERE id=%s",
                    (ticket["id"],),
                )
                tickets.event(
                    db,
                    ticket,
                    "started",
                    "Implementation and validation are in progress.",
                    existing,
                )
                if i >= 4:
                    row = recommendations.get_finding(db, workspace, row["id"])
                    recommendations.change(
                        db,
                        workspace,
                        row["id"],
                        SimpleNamespace(
                            version=row["version"],
                            action="execute",
                            note="Configuration updated and operational checks completed.",
                        ),
                        existing,
                    )
        db.commit()
    return {
        "workspace": workspace,
        "resources": len(scenarios(now)),
        "samples": count,
        "findings": db.execute(
            "SELECT count(*) AS n FROM recommendation_findings WHERE organization_id=%s",
            (workspace,),
        ).fetchone()["n"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=UUID,
        default=UUID(WORKSPACE),
        help="Existing destination workspace UUID",
    )
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as db:
        print(seed(db, str(args.workspace)))


if __name__ == "__main__":
    main()
