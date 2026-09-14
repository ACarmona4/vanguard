from __future__ import annotations

import base64
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import CollectionResult


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def write_snapshot(result: CollectionResult, provider: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    target = output_dir / f"{provider}_{timestamp}.json"
    temporary = target.with_suffix(".json.tmp")
    payload = {
        "provider": provider,
        "created_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "resource_count": len(result.resources),
        "errors": result.errors,
        "resources": [resource.to_dict() for resource in result.resources],
    }
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=json_default) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target
