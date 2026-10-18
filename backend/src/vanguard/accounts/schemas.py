"""Request and response contracts for accounts."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]


EMAIL_PATTERN = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"


class SignUpRequest(BaseModel):
    full_name: DisplayName
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=10, max_length=256)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=256)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    password: str = Field(min_length=10, max_length=256)


class UserProfileUpdate(BaseModel):
    full_name: DisplayName
    theme: Literal["light", "dark"]


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    theme: Literal["light", "dark"]
    created_at: datetime
    updated_at: datetime
    organization_id: UUID
    organization_name: str
    role: Literal["owner", "admin", "member", "viewer"]


class AuthResponse(BaseModel):
    user: UserResponse
    csrf_token: str
