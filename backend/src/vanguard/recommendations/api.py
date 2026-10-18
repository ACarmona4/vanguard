"""Persistent recommendations and explicit, role-checked workflow actions."""

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from ..accounts.auth import reserve_attempt
from ..organizations.repository import ADMIN_ROLES
from ..utilization.worker import metrics_worker
from . import repository
from .engine import evaluate
from .policy import DEFAULT_POLICY, SOURCES
from .schemas import (
    Decision,
    FindingDetail,
    PriorityChange,
    RecommendationQuery,
    Report,
    StoredFinding,
)
from .worker import collect_workspace


def build_report(connection, organization_id, query):
    saved = connection.execute(
        "SELECT evaluated_at, report FROM recommendation_evaluations WHERE organization_id = %s",
        (organization_id,),
    ).fetchone()
    now = datetime.now(timezone.utc)
    if saved:
        report = dict(saved["report"])
        report["generated_at"] = saved["evaluated_at"]
        report["analysis_status"] = (
            "stale"
            if (now - saved["evaluated_at"]).total_seconds() > 900
            else "current"
        )
        if report["analysis_status"] == "stale":
            report["notices"] = [
                *report["notices"],
                "Results are more than 15 minutes old. Run a new analysis to update them.",
            ]
    else:
        _, summary = evaluate([], [], [])
        report = {
            "generated_at": now,
            "analysis_status": "pending",
            "telemetry_status": "unavailable",
            "notices": [
                "No results yet. Run an analysis to get started."
            ],
            "collection": metrics_worker.snapshot(str(organization_id)),
            "summary": summary,
            "policy": asdict(DEFAULT_POLICY),
            "sources": SOURCES,
        }
    report["summary"] = {
        **report["summary"],
        **repository.open_summary(connection, organization_id, query),
    }
    items, total = repository.list_findings(connection, organization_id, query)
    return {
        **report,
        "items": items,
        "total": total,
        "limit": query.limit,
        "offset": query.offset,
    }


def create_router(
    database_dependency, write_dependency, user_dependency, operator_dependency
):
    router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])

    @router.get("", response_model=Report)
    def recommendations(
        connection: database_dependency,
        user: user_dependency,
        query: Annotated[RecommendationQuery, Query()],
    ):
        return build_report(connection, user["organization_id"], query)

    @router.get("/summary", response_model=Report)
    def summary(connection: database_dependency, user: user_dependency):
        return build_report(
            connection, user["organization_id"], RecommendationQuery(limit=3)
        )

    @router.post("/sync", response_model=Report)
    def sync(connection: write_dependency, user: operator_dependency):
        if not reserve_attempt(
            connection,
            "recommendation-analysis",
            str(user["organization_id"]),
            limit=5,
            window_seconds=300,
        ):
            raise HTTPException(
                429,
                "Too many analysis requests. Try again in a few minutes.",
            )
        collect_workspace(connection, user["organization_id"])
        return build_report(connection, user["organization_id"], RecommendationQuery())

    @router.get("/{finding_id}", response_model=FindingDetail)
    def detail(
        finding_id: UUID,
        connection: database_dependency,
        user: user_dependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        row = repository.get_finding(connection, user["organization_id"], finding_id)
        if not row:
            raise HTTPException(404, "Recommendation not found")
        events, total = repository.history(
            connection, user["organization_id"], finding_id, limit, offset
        )
        return {
            "finding": repository.public_finding(row),
            "events": events,
            "total_events": total,
            "limit": limit,
            "offset": offset,
        }

    def apply_change(connection, user, finding_id, payload, *, prioritize=False):
        try:
            result = repository.change(
                connection,
                user["organization_id"],
                finding_id,
                payload,
                user,
                prioritize=prioritize,
            )
        except repository.Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        if result is None:
            raise HTTPException(404, "Recommendation not found")
        connection.commit()
        return result

    @router.put("/{finding_id}/priority", response_model=StoredFinding)
    def prioritize(
        finding_id: UUID,
        payload: PriorityChange,
        connection: write_dependency,
        user: operator_dependency,
    ):
        return apply_change(connection, user, finding_id, payload, prioritize=True)

    @router.post("/{finding_id}/decisions", response_model=StoredFinding)
    def decide(
        finding_id: UUID,
        payload: Decision,
        connection: write_dependency,
        user: operator_dependency,
    ):
        if payload.action in {"approve", "reject"} and user["role"] not in ADMIN_ROLES:
            raise HTTPException(
                403, "An administrator must approve or reject this action."
            )
        return apply_change(connection, user, finding_id, payload)

    return router
