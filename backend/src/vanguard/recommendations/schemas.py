"""Public, read-only recommendation contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..inventory.schemas import ResourceFilters
from ..utilization.schemas import CollectionStatus

Kind = Literal["alert", "recommendation", "suggestion"]
Severity = Literal["critical", "warning", "info"]
Category = Literal["performance", "capacity", "cost", "reliability", "observability"]


class RecommendationQuery(ResourceFilters):
    provider: Literal["aws", "gcp", "all"] = "all"
    kind: Kind | None = None
    severity: Severity | None = None
    category: Category | None = None
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
    provider: str
    resource_type: str
    scope_id: str | None
    region: str | None
    evidence: Evidence


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
    telemetry_status: Literal["available", "partial", "unavailable"]
    notices: list[str]
    collection: CollectionStatus
    summary: RecommendationSummary
    items: list[Finding]
    total: int
    limit: int
    offset: int
    policy: dict[str, float | int]
    sources: list[Source]
