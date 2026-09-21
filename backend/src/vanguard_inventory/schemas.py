"""Request and response contracts for the inventory API."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ResourceFilters(BaseModel):
    provider: Literal["aws", "gcp", "all"] = "aws"
    resource_type: str | None = None
    region: str | None = None
    scope_id: str | None = None
    status: str | None = None


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


class AWSCollectionStatus(BaseModel):
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


class AWSConnectionCreate(BaseModel):
    provider: Literal["aws"]
    name: str = Field(min_length=1, max_length=120)
    regions: list[str] = Field(min_length=1)
    access_key_id: str = Field(min_length=16, max_length=128)
    secret_access_key: str = Field(min_length=32, max_length=256)
    session_token: str | None = Field(default=None, max_length=8192)

    @field_validator("regions")
    @classmethod
    def normalize_regions(cls, value: list[str]) -> list[str]:
        regions = list(dict.fromkeys(region.strip() for region in value if region.strip()))
        if not regions:
            raise ValueError("Debes seleccionar al menos una región")
        return regions


class GCPConnectionCreate(BaseModel):
    provider: Literal["gcp"]
    name: str = Field(min_length=1, max_length=120)
    project_id: str = Field(min_length=6, max_length=64, pattern=r"^[a-z][a-z0-9-]+[a-z0-9]$")
    service_account_json: dict


CloudConnectionCreate = AWSConnectionCreate | GCPConnectionCreate


class CloudConnectionResponse(BaseModel):
    id: UUID
    provider: Literal["aws", "gcp"]
    name: str
    scope_id: str
    regions: list[str]
    credential_hint: str
    identity: str
    enabled: bool
    status: Literal["ready", "syncing", "success", "partial", "error"]
    last_error: str | None
    last_tested_at: datetime | None
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime
