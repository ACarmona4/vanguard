"""Owner-scoped reports evaluated from existing inventory and Prometheus."""

import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Query
from psycopg import sql

from ..inventory.repository import _where
from ..utilization.catalog import metrics_for
from ..utilization.service import MetricsUnavailable, query_series
from ..utilization.worker import metrics_worker
from .engine import INACTIVE, evaluate
from .policy import DEFAULT_POLICY, SOURCES
from .schemas import RecommendationQuery, Report
from .telemetry import query_history


def build_report(connection, owner_id, query):
    where, values = _where(query, owner_id)
    rows = connection.execute(sql.SQL(
        "SELECT id, owner_id, provider, scope_id, region, resource_type, resource_id, "
        "name, status, attributes, jsonb_build_object('Attachments', raw_data->'Attachments', "
        "'settings', jsonb_build_object('dataDiskSizeGb', raw_data#>'{{settings,dataDiskSizeGb}}')) AS raw_data, "
        "last_seen_at FROM inventory_resources WHERE {} ORDER BY id"
    ).format(where), values).fetchall()
    metric_rows = [row for row in rows if metrics_for(row) and (row.get("status") or "").lower() not in INACTIVE]
    now = time.time()
    recent, history, notices = [], [], []
    available, historical = True, True
    try:
        recent = query_series(metric_rows)
    except MetricsUnavailable:
        available = False
        notices.append("Live telemetry is unavailable. Utilization alerts and optimization checks could not be evaluated; this does not mean resources are healthy.")
    if available:
        try:
            history = query_history(metric_rows, now=now)
        except MetricsUnavailable as exc:
            historical = False
            notices.append(str(exc))
    items, summary = evaluate(rows, recent, history, now=now,
                              telemetry_available=available, history_available=historical)
    collection = metrics_worker.snapshot(owner_id)
    if collection["status"] in {"partial", "error", "disabled"}:
        notices.append("Metric collection needs attention. Check Utilization for collection details.")
    filtered = [item for item in items if all(
        getattr(query, key) is None or item[key] == getattr(query, key)
        for key in ("kind", "severity", "category")
    )]
    return {
        "generated_at": datetime.fromtimestamp(now, timezone.utc),
        "telemetry_status": "unavailable" if not available else "partial" if not historical or collection["status"] in {"partial", "error", "disabled"} else "available",
        "notices": notices, "collection": collection, "summary": summary,
        "items": filtered[query.offset:query.offset + query.limit], "total": len(filtered),
        "offset": query.offset, "limit": query.limit,
        "policy": asdict(DEFAULT_POLICY), "sources": SOURCES,
    }


def create_router(database_dependency, user_dependency):
    router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])

    @router.get("", response_model=Report)
    def recommendations(connection: database_dependency, user: user_dependency,
                        query: Annotated[RecommendationQuery, Query()]):
        return build_report(connection, str(user["organization_id"]), query)

    @router.get("/summary", response_model=Report)
    def summary(connection: database_dependency, user: user_dependency):
        return build_report(connection, str(user["organization_id"]), RecommendationQuery(limit=3))

    return router
