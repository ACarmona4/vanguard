"""Request and response contracts for labs."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class LabDeploymentCreate(BaseModel):
    connection_id: UUID
    region: str | None = Field(default=None, min_length=3, max_length=64)


class LabDeploymentResponse(BaseModel):
    id: UUID
    connection_id: UUID
    provider: Literal["aws", "gcp"]
    region: str
    status: Literal["queued", "deploying", "active", "destroying", "error"]
    outputs: dict[str, Any]
    last_error: str | None
    created_at: datetime
    updated_at: datetime
