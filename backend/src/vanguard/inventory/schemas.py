"""Request and response contracts for inventory."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ResourceFilters(BaseModel):
    provider: Literal["aws", "gcp", "all"] = "aws"
    resource_type: str | None = Field(default=None, max_length=128)
    region: str | None = Field(default=None, max_length=64)
    scope_id: str | None = Field(default=None, max_length=256)
    status: str | None = Field(default=None, max_length=64)


class ResourceQuery(ResourceFilters):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ResourceResponse(BaseModel):
    id: int
    provider: str
    resource_type: str
    resource_id: str
    name: str | None
    scope_id: str | None
    region: str | None
    zone: str | None
    status: str | None
    attributes: dict[str, Any]
    first_seen_at: datetime
    last_seen_at: datetime


class ResourcePage(BaseModel):
    items: list[ResourceResponse]
    total: int
    limit: int
    offset: int


class ResourceCount(BaseModel):
    value: str | None
    count: int


class InventorySummary(BaseModel):
    total: int
    by_type: list[ResourceCount]
    by_region: list[ResourceCount]
    by_account: list[ResourceCount]


class CloudCollectionStatus(BaseModel):
    enabled: bool
    running: bool
    status: Literal["idle", "running", "success", "partial", "error", "disabled"]
    interval_seconds: int
    last_started_at: datetime | None
    last_finished_at: datetime | None
    last_success_at: datetime | None
    resources_processed: int
    resources_deleted: int
    error_count: int
