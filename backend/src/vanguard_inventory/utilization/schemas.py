"""Validated API contracts for infrastructure utilization."""

from datetime import datetime
from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, Field

from ..schemas import ResourceFilters


class UtilizationQuery(ResourceFilters):
    provider: Literal["aws", "gcp", "all"] = "all"
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class HistoryHours(IntEnum):
    hour = 1
    six_hours = 6
    day = 24


class MetricReading(BaseModel):
    key: str
    label: str
    unit: str
    value: float | None
    last_value: float | None
    observed_at: datetime | None
    status: Literal["ok", "stale", "no_data"]
    requires_agent: bool
    automatically_managed: bool = False
    points: list[tuple[float, float]]


class ResourceUtilization(BaseModel):
    id: int
    name: str | None
    provider: str
    scope_id: str | None
    region: str | None
    resource_type: str
    resource_id: str
    metrics: list[MetricReading]


class CollectionStatus(BaseModel):
    status: Literal["pending", "disabled", "success", "partial", "error"]
    last_finished_at: datetime | None
    errors: list[str]


class UtilizationPage(BaseModel):
    items: list[ResourceUtilization]
    total: int
    limit: int
    offset: int
    collection: CollectionStatus


class UtilizationSummary(BaseModel):
    eligible: int
    monitored: int
    without_data: int
    cpu_average: float | None
    cpu_samples: int
    memory_average: float | None
    memory_samples: int
    high_utilization: int
    collection: CollectionStatus
