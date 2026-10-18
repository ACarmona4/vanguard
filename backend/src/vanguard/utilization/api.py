"""Utilization routes use the existing read-only database dependency."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from psycopg import sql

from ..inventory.repository import _where
from ..inventory.schemas import ResourceFilters
from .catalog import AWS, GCP, metrics_for
from .schemas import (
    HistoryHours,
    ResourceUtilization,
    UtilizationPage,
    UtilizationQuery,
    UtilizationSummary,
)
from .service import MetricsUnavailable, describe, query_series, summarize
from .worker import metrics_worker


def create_router(database_dependency, user_dependency):
    router = APIRouter(prefix="/api/utilization", tags=["utilization"])

    @router.get("", response_model=UtilizationPage)
    def utilization(
        connection: database_dependency,
        user: user_dependency,
        query: Annotated[UtilizationQuery, Query()],
    ):
        selected = _resources(connection, query, str(user["organization_id"]), limit=query.limit, offset=query.offset)
        where, values = _metric_where(query, str(user["organization_id"]))
        total = connection.execute(sql.SQL("SELECT count(*) AS total FROM inventory_resources WHERE {}").format(where), values).fetchone()["total"]
        try:
            items = describe(selected, query_series(selected))
        except MetricsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"items": items, "total": total, "limit": query.limit, "offset": query.offset,
                "collection": metrics_worker.snapshot(str(user["organization_id"]))}

    @router.get("/summary", response_model=UtilizationSummary)
    def summary(connection: database_dependency, user: user_dependency):
        rows = _resources(connection, ResourceFilters(provider="all"), str(user["organization_id"]))
        try:
            return {**summarize(describe(rows, query_series(rows))), "collection": metrics_worker.snapshot(str(user["organization_id"]))}
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
            f"SELECT {METRIC_COLUMNS} FROM inventory_resources WHERE id = %s AND owner_id = %s",
            (resource_id, user["organization_id"]),
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


METRIC_COLUMNS = "id, owner_id, provider, scope_id, region, resource_type, resource_id, name, attributes"


def _metric_where(filters, owner_id):
    where, values = _where(filters, owner_id)
    supported = sql.SQL("((provider = 'aws' AND resource_type = ANY(%(aws_types)s)) OR (provider = 'gcp' AND resource_type = ANY(%(gcp_types)s)))")
    return sql.SQL("{} AND {}").format(where, supported), {**values, "aws_types": list(AWS), "gcp_types": list(GCP)}


def _resources(connection, filters, owner_id, *, limit=None, offset=0):
    where, values = _metric_where(filters, owner_id)
    paging = sql.SQL(" LIMIT %(limit)s OFFSET %(offset)s") if limit is not None else sql.SQL("")
    query = sql.SQL(f"SELECT {METRIC_COLUMNS} FROM inventory_resources WHERE {{}} ORDER BY resource_type, name, id").format(where) + paging
    return connection.execute(query, {**values, "limit": limit, "offset": offset}).fetchall()
