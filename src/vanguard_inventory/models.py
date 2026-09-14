from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class Resource:
    provider: str
    resource_type: str
    resource_id: str
    name: str | None = None
    scope_id: str | None = None
    region: str | None = None
    zone: str | None = None
    status: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    raw_data: dict[str, Any] = field(default_factory=dict)
    collected_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["collected_at"] = self.collected_at.isoformat()
        return value


@dataclass(slots=True)
class CollectionResult:
    resources: list[Resource] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def extend(self, resources: list[Resource]) -> None:
        self.resources.extend(resources)

