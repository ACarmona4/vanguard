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


def create_router(database_dependency):
    router = APIRouter(prefix="/api/utilization", tags=["utilization"])

    @router.get("", response_model=UtilizationPage)
    def utilization(connection: database_dependency, query: Annotated[UtilizationQuery, Query()]):
        rows = _resources(connection, query)
        selected = rows[query.offset:query.offset + query.limit]
        try:
            items = describe(selected, query_series(selected))
        except MetricsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"items": items, "total": len(rows), "limit": query.limit, "offset": query.offset,
                "collection": metrics_worker.snapshot()}

    @router.get("/summary", response_model=UtilizationSummary)
    def summary(connection: database_dependency):
        rows = _resources(connection, ResourceFilters(provider="all"))
        try:
            return {**summarize(describe(rows, query_series(rows))), "collection": metrics_worker.snapshot()}
        except MetricsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/{resource_id}", response_model=ResourceUtilization)
    def history(resource_id: int, connection: database_dependency, hours: HistoryHours = HistoryHours.hour):
        resource = connection.execute("SELECT * FROM inventory_resources WHERE id = %s", (resource_id,)).fetchone()
        if not resource:
            raise HTTPException(status_code=404, detail="Recurso no encontrado")
        if not metrics_for(resource):
            raise HTTPException(status_code=422, detail="Las métricas de utilización no aplican a este recurso")
        try:
            return describe([resource], query_series([resource], minutes=hours * 60), history=True)[0]
        except MetricsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return router


def _resources(connection, filters):
    where, values = _where(filters)
    rows = connection.execute(sql.SQL("SELECT * FROM inventory_resources WHERE {} ORDER BY resource_type, name, id").format(where), values).fetchall()
    return [row for row in rows if metrics_for(row)]
