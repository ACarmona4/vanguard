"""Utilization routes use the existing read-only database dependency."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from psycopg import sql

from ..repository import _where
from ..schemas import ResourceFilters
from .catalog import metrics_for
from .service import MetricsUnavailable, describe, query_series, summarize
from .worker import metrics_worker
from .schemas import HistoryHours, ResourceUtilization, UtilizationPage, UtilizationQuery, UtilizationSummary


def create_router(database_dependency, user_dependency):
    router = APIRouter(prefix="/api/utilization", tags=["utilization"])

    @router.get("", response_model=UtilizationPage)
    def utilization(
        connection: database_dependency,
        user: user_dependency,
        query: Annotated[UtilizationQuery, Query()],
    ):
        rows = _resources(connection, query, str(user["id"]))
        selected = rows[query.offset:query.offset + query.limit]
        try:
            items = describe(selected, query_series(selected))
        except MetricsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"items": items, "total": len(rows), "limit": query.limit, "offset": query.offset,
                "collection": metrics_worker.snapshot(str(user["id"]))}

    @router.get("/summary", response_model=UtilizationSummary)
    def summary(connection: database_dependency, user: user_dependency):
        rows = _resources(connection, ResourceFilters(provider="all"), str(user["id"]))
        try:
            return {**summarize(describe(rows, query_series(rows))), "collection": metrics_worker.snapshot(str(user["id"]))}
        except MetricsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/{resource_id}", response_model=ResourceUtilization)
    def history(
        resource_id: int,
        connection: database_dependency,
        user: user_dependency,
        hours: HistoryHours = HistoryHours.hour,
    ):
        resource = connection.execute(
            "SELECT * FROM inventory_resources WHERE id = %s AND owner_id = %s",
            (resource_id, user["id"]),
        ).fetchone()
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")
        if not metrics_for(resource):
            raise HTTPException(status_code=422, detail="Utilization metrics do not apply to this resource")
        try:
            return describe([resource], query_series([resource], minutes=hours * 60), history=True)[0]
        except MetricsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return router


def _resources(connection, filters, owner_id):
    where, values = _where(filters, owner_id)
    rows = connection.execute(sql.SQL("SELECT * FROM inventory_resources WHERE {} ORDER BY resource_type, name, id").format(where), values).fetchall()
    return [row for row in rows if metrics_for(row)]
