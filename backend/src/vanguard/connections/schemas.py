"""Request and response contracts for connections."""

import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, field_validator


class AWSConnectionCreate(BaseModel):
    provider: Literal["aws"]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    regions: list[str] = Field(min_length=1, max_length=32)
    access_key_id: str = Field(min_length=16, max_length=128)
    secret_access_key: str = Field(min_length=32, max_length=256)
    session_token: str | None = Field(default=None, max_length=8192)

    @field_validator("regions")
    @classmethod
    def normalize_regions(cls, value: list[str]) -> list[str]:
        regions = list(dict.fromkeys(region.strip() for region in value if region.strip()))
        if not regions:
            raise ValueError("You must select at least one region")
        if any(len(region) > 64 or not re.fullmatch(r"[a-z]{2}(?:-[a-z0-9]+)+-\d+", region) for region in regions):
            raise ValueError("Use valid AWS region names")
        return regions


class GCPConnectionCreate(BaseModel):
    provider: Literal["gcp"]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
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
