"""Persistent, organization-scoped presentation telemetry, replayed on a rolling clock.

Cloud workspaces continue using Prometheus. Only explicitly registered resources
can read this store; the seed command is the sole writer of generated samples.
"""

import math
import os
import time
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

OWNERS = set()
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS presentation_workspaces (
 owner_id UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
 billing_date DATE NOT NULL DEFAULT CURRENT_DATE
);
CREATE TABLE IF NOT EXISTS presentation_metrics (
 owner_id UUID NOT NULL REFERENCES presentation_workspaces(owner_id) ON DELETE CASCADE,
 inventory_id BIGINT NOT NULL REFERENCES inventory_resources(id) ON DELETE CASCADE,
 metric TEXT NOT NULL, samples JSONB NOT NULL,
 PRIMARY KEY(owner_id,inventory_id,metric)
);
"""


def ensure_schema(db):
    db.execute(SCHEMA_SQL)
    OWNERS.update(
        str(row["owner_id"] if isinstance(row, dict) else row[0])
        for row in db.execute("SELECT owner_id FROM presentation_workspaces").fetchall()
    )


def register(owner):
    OWNERS.add(str(owner))


def snapshot(owner):
    if str(owner) in OWNERS:
        return {
            "status": "success",
            "last_finished_at": datetime.now(timezone.utc).isoformat(),
            "errors": [],
        }
    return None


def refresh(db, owner):
    row = db.execute(
        "SELECT billing_date FROM presentation_workspaces WHERE owner_id=%s FOR UPDATE",
        (owner,),
    ).fetchone()
    if not row:
        return
    register(owner)
    db.execute(
        "UPDATE inventory_resources SET last_seen_at=now() WHERE owner_id=%s AND attributes->>'telemetry_profile'='commerce-production'",
        (owner,),
    )
    db.execute(
        "UPDATE cost_records SET usage_date=usage_date + (CURRENT_DATE - %s), collected_at=now() WHERE owner_id=%s "
        "AND connection_id IN (SELECT connection_id FROM inventory_resources WHERE owner_id=%s AND attributes->>'telemetry_profile'='commerce-production')",
        (row["billing_date"], owner, owner),
    )
    db.execute(
        "UPDATE presentation_workspaces SET billing_date=CURRENT_DATE WHERE owner_id=%s",
        (owner,),
    )
    db.execute(
        "UPDATE cost_sources SET last_synced_at=now() WHERE owner_id=%s AND connection_id IN "
        "(SELECT connection_id FROM inventory_resources WHERE owner_id=%s AND attributes->>'telemetry_profile'='commerce-production')",
        (owner, owner),
    )
    db.execute(
        "UPDATE cloud_connections SET last_synced_at=now() WHERE owner_id=%s AND id IN "
        "(SELECT connection_id FROM inventory_resources WHERE owner_id=%s AND attributes->>'telemetry_profile'='commerce-production')",
        (owner, owner),
    )


def series(resources, *, minutes=30, hourly=False, now=None):
    """Return None for cloud resources; never fall back to another tenant's samples."""
    if not resources or not any(
        (r.get("attributes") or {}).get("telemetry_profile") for r in resources
    ):
        return None
    owners = {str(r.get("owner_id") or "") for r in resources}
    if len(owners) != 1 or not next(iter(owners)):
        raise ValueError("Presentation telemetry requires a single organization")
    owner = owners.pop()
    with psycopg.connect(
        os.environ["DATABASE_URL"], row_factory=dict_row, connect_timeout=5
    ) as db:
        registered = db.execute(
            "SELECT owner_id FROM presentation_workspaces WHERE owner_id=%s", (owner,)
        ).fetchone()
        if not registered:
            raise ValueError("Telemetry dataset is not registered")
        rows = db.execute(
            "SELECT inventory_id,metric,samples FROM presentation_metrics WHERE owner_id=%s AND inventory_id=ANY(%s)",
            (owner, [r["id"] for r in resources]),
        ).fetchall()
    register(owner)
    end = int(now if now is not None else time.time()) // 300 * 300
    identities = {r["id"]: r for r in resources}
    result = []
    from ..utilization.catalog import IDENTITY_LABELS

    for row in rows:
        resource = identities[row["inventory_id"]]
        samples = row["samples"]
        points = [
            (end - (len(samples) - 1 - i) * 300, value)
            for i, value in enumerate(samples)
            if (len(samples) - 1 - i) * 300 <= minutes * 60 and math.isfinite(value)
        ]
        if hourly:
            buckets = {}
            for stamp, value in points:
                # Completed hour buckets have twelve distinct 5-minute readings.
                bucket = (stamp // 3600 + 1) * 3600
                if bucket <= end // 3600 * 3600:
                    buckets.setdefault(bucket, []).append(value)
            aggregate = min if row["metric"].startswith("provisioned_") else max
            points = [
                (stamp, aggregate(values))
                for stamp, values in sorted(buckets.items())
                if len(values) >= 6
            ]
        result.append(
            {
                "metric": {
                    **{k: str(resource.get(k) or "") for k in IDENTITY_LABELS},
                    "__name__": "vanguard_" + row["metric"],
                },
                "values": points,
            }
        )
    return result
