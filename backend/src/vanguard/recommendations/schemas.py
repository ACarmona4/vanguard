"""Recommendation evidence, prioritization, and auditable workflow contracts."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..inventory.schemas import ResourceFilters
from ..utilization.schemas import CollectionStatus

Kind = Literal["alert", "recommendation", "suggestion"]
Severity = Literal["critical", "warning", "info"]
Category = Literal["performance", "capacity", "cost", "reliability", "observability"]
Priority = Literal["critical", "high", "medium", "low"]
Relevance = Literal["low", "medium", "high"]
EconomicImpact = Literal["unknown", "low", "medium", "high"]
WorkflowStatus = Literal["detected", "approved", "executed", "rejected"]


class RecommendationQuery(ResourceFilters):
    provider: Literal["aws", "gcp", "all"] = "all"
    kind: Kind | None = None
    severity: Severity | None = None
    category: Category | None = None
    priority: Priority | None = None
    workflow_status: Literal[
        "open", "all", "detected", "approved", "executed", "rejected"
    ] = "open"
    order_by: Literal["priority", "economic_impact", "business_relevance", "newest"] = (
        "priority"
    )
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class Evidence(BaseModel):
    metric: str
    value: float | None = None
    unit: str = ""
    threshold: float | None = None
    samples: int = 0
    window: str
    observed_at: datetime | None = None
    explanation: str


class Finding(BaseModel):
    id: str
    rule: str
    kind: Kind
    severity: Severity
    category: Category
    title: str
    description: str
    actions: list[str]
    resource_id: int
    resource_name: str
    cloud_resource_id: str
    provider: str
    resource_type: str
    scope_id: str | None
    region: str | None
    resource_status: str | None = None
    evidence: Evidence


class StoredFinding(Finding):
    id: UUID
    detection_key: str
    workflow_status: WorkflowStatus
    is_current: bool
    first_detected_at: datetime
    last_detected_at: datetime
    version: int
    business_relevance: Relevance
    economic_impact: EconomicImpact
    priority: Priority
    priority_score: int
    priority_reason: str


class WorkflowEvent(BaseModel):
    id: int
    event_type: Literal[
        "detected",
        "condition_cleared",
        "prioritized",
        "approved",
        "executed",
        "rejected",
    ]
    actor_id: UUID | None
    actor_name: str
    created_at: datetime
    note: str
    snapshot: StoredFinding


class FindingDetail(BaseModel):
    finding: StoredFinding
    events: list[WorkflowEvent]
    total_events: int
    limit: int
    offset: int


class WorkflowChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    note: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)
    ]


class PriorityChange(WorkflowChange):
    business_relevance: Relevance
    economic_impact: EconomicImpact


class Decision(WorkflowChange):
    action: Literal["approve", "execute", "reject"]


class RecommendationSummary(BaseModel):
    resources: int
    eligible: int
    monitored: int
    without_data: int
    history_ready: int
    affected_resources: int
    cost_opportunities: int
    alerts: int
    recommendations: int
    suggestions: int
    critical: int
    warning: int
    info: int


class Source(BaseModel):
    title: str
    url: str


class Report(BaseModel):
    generated_at: datetime
    analysis_status: Literal["pending", "current", "stale"]
    telemetry_status: Literal["available", "partial", "unavailable"]
    notices: list[str]
    collection: CollectionStatus
    summary: RecommendationSummary
    items: list[StoredFinding]
    total: int
    limit: int
    offset: int
    policy: dict[str, float | int]
    sources: list[Source]
