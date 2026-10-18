"""Owner-scoped reports evaluated from existing inventory and Prometheus."""

import time
from dataclasses import asdict
from datetime import datetime, timezone

from psycopg import sql

from ..inventory.repository import _where
from ..inventory.schemas import ResourceFilters
from ..utilization.catalog import metrics_for
from ..utilization.service import MetricsUnavailable, query_series
from ..utilization.worker import metrics_worker
from .engine import INACTIVE, confirmed_checks, evaluate
from .policy import DEFAULT_POLICY, SOURCES
from .telemetry import query_history


def evaluate_inventory(connection, owner_id):
    query = ResourceFilters(provider="all")
    where, values = _where(query, owner_id)
    rows = connection.execute(
        sql.SQL(
            "SELECT id, owner_id, provider, scope_id, region, resource_type, resource_id, "
            "name, status, attributes, jsonb_build_object('Attachments', raw_data->'Attachments', "
            "'settings', jsonb_build_object('dataDiskSizeGb', raw_data#>'{{settings,dataDiskSizeGb}}')) AS raw_data, "
            "last_seen_at FROM inventory_resources WHERE {} ORDER BY id"
        ).format(where),
        values,
    ).fetchall()
    connection.commit()  # Release the inventory snapshot before waiting for Prometheus.
    metric_rows = [
        row
        for row in rows
        if metrics_for(row) and (row.get("status") or "").lower() not in INACTIVE
    ]
    now = time.time()
    recent, history, notices = [], [], []
    available, historical = True, True
    try:
        recent = query_series(metric_rows)
    except MetricsUnavailable:
        available = False
        notices.append(
            "Metrics are unavailable. Check the cloud connection and try again."
        )
    if available:
        try:
            history = query_history(metric_rows, now=now)
        except MetricsUnavailable as exc:
            historical = False
            notices.append(str(exc))
    items, summary = evaluate(
        rows,
        recent,
        history,
        now=now,
        telemetry_available=available,
        history_available=historical,
    )
    collection = metrics_worker.snapshot(owner_id)
    if collection["status"] in {"partial", "error", "disabled"}:
        notices.append(
            "Some metrics are unavailable. Check Utilization for details."
        )
    return {
        "generated_at": datetime.fromtimestamp(now, timezone.utc),
        "telemetry_status": "unavailable"
        if not available
        else "partial"
        if not historical or collection["status"] in {"partial", "error", "disabled"}
        else "available",
        "notices": notices,
        "collection": collection,
        "summary": summary,
        "items": items,
        "checked_keys": confirmed_checks(
            rows,
            recent,
            history,
            now=now,
            telemetry_available=available,
            history_available=historical,
        ),
        "policy": asdict(DEFAULT_POLICY),
        "sources": SOURCES,
    }
