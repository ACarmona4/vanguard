from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from .collectors import validate_gcp_table


class CostQuery(BaseModel):
    days: Literal[7, 30, 90] = 30
    provider: Literal["aws", "gcp", "all"] = "all"


class CostSourceUpdate(BaseModel):
    billing_export_table: str = Field(min_length=10, max_length=512)
    billing_location: str = Field(default="US", min_length=2, max_length=32, pattern=r"^[A-Za-z0-9-]+$")

    @field_validator("billing_export_table")
    @classmethod
    def valid_table(cls, value: str) -> str:
        return validate_gcp_table(value)


class CostSourceResponse(BaseModel):
    connection_id: UUID
    provider: Literal["aws", "gcp"]
    connection_name: str
    scope_id: str
    billing_export_table: str | None
    billing_location: str | None
    enabled: bool
    status: Literal["unconfigured", "ready", "syncing", "success", "error"]
    detail_available: bool
    last_error: str | None
    last_synced_at: datetime | None
    updated_at: datetime


class CostTotal(BaseModel):
    currency: str
    amount: float
    previous_amount: float
    change_percent: float | None


class CostAmount(BaseModel):
    currency: str
    amount: float


class CostBreakdown(CostAmount):
    name: str


class CostTrendPoint(CostAmount):
    date: date


class CostResource(CostAmount):
    provider: str
    service: str
    category: str
    resource_id: str
    resource_name: str | None
    region: str | None


class CostPeriod(BaseModel):
    start: date
    end: date
    days: int


class CostOverview(BaseModel):
    period: CostPeriod
    totals: list[CostTotal]
    daily_average: list[CostAmount]
    by_provider: list[CostBreakdown]
    by_category: list[CostBreakdown]
    top_services: list[CostBreakdown]
    trend: list[CostTrendPoint]
    top_resources: list[CostResource]
    service_count: int
    estimated: bool
    sources: list[CostSourceResponse]
