"""Organization and membership contracts."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from ..accounts.schemas import EMAIL_PATTERN

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)
]
AssignableRole = Literal["admin", "member", "viewer"]


class OrganizationCreate(BaseModel):
    name: Name


class OrganizationResponse(BaseModel):
    id: UUID
    name: str
    role: Literal["owner", "admin", "member", "viewer"]
    created_at: datetime


class MemberCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)
    role: AssignableRole = "viewer"


class MemberUpdate(BaseModel):
    role: AssignableRole


class MemberResponse(BaseModel):
    user_id: UUID
    full_name: str
    email: str
    role: Literal["owner", "admin", "member", "viewer"]
    created_at: datetime
