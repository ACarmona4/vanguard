"""Join Prometheus data to real inventory identities; missing data stays missing."""

import json
import math
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen

from .catalog import IDENTITY_LABELS, identity, metrics_for

FRESH_SECONDS = 1200  # Cloud APIs can lag several minutes, EC2 basic period is 5 min.


class MetricsUnavailable(Exception):
    pass


def query_series(resources, minutes=30):
    if not resources:
        return []
    # Values always originate in the database and are escaped as PromQL string literals.
    selectors = ['__name__=~"vanguard_.+"']
    if len(resources) == 1:
        selectors.extend(f"{label}={json.dumps(value)}" for label, value in zip(IDENTITY_LABELS, identity(resources[0])))
    query = "{" + ",".join(selectors) + "}[" + str(minutes) + "m]"
    base = os.getenv("VANGUARD_PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")
    try:
        with urlopen(base + "/api/v1/query?" + urlencode({"query": query, "timeout": "8s"}), timeout=10) as response:
            body = json.load(response)
        if body.get("status") != "success" or body["data"]["resultType"] != "matrix":
            raise ValueError("Invalid metric response")
        return body["data"]["result"]
    except Exception as exc:
        raise MetricsUnavailable("Prometheus no está disponible. Inicia el servicio de observabilidad.") from exc


def indexed_series(series):
    indexed = {}
    for item in series:
        labels = item["metric"]
        key = identity(labels)
        metric = labels.get("__name__", "").removeprefix("vanguard_")
        points = [(float(timestamp), float(value)) for timestamp, value in item.get("values", [])
                  if math.isfinite(float(value))]
        if points:
            # Duplicate identities (e.g. an agent restart) are merged by timestamp.
            indexed.setdefault((key, metric), {}).update(points)
    return indexed


def describe(resources, series, *, history=False, now=None):
    now = now if now is not None else time.time()
    indexed = indexed_series(series)
    result = []
    for resource in resources:
        metrics = []
        for definition in metrics_for(resource):
            points = sorted(indexed.get((identity(resource), definition.key), {}).items())
            latest = points[-1] if points else None
            fresh = latest is not None and 0 <= now - latest[0] <= (120 if definition.agent and not definition.namespace else FRESH_SECONDS)
            metrics.append({
                "key": definition.key, "label": definition.label, "unit": definition.unit,
                "value": latest[1] if fresh else None,
                "last_value": latest[1] if latest else None,
                "observed_at": datetime.fromtimestamp(latest[0], timezone.utc).isoformat() if latest else None,
                "status": "ok" if fresh else "stale" if latest else "no_data",
                "requires_agent": definition.agent,
                "automatically_managed": definition.agent and resource["provider"] == "aws"
                    and resource.get("attributes", {}).get("tags", {}).get("VanguardAgent") == "managed",
                "points": [[timestamp * 1000, value] for timestamp, value in points] if history else [],
            })
        result.append({**{key: resource.get(key) for key in ("id", "name", *IDENTITY_LABELS)}, "metrics": metrics})
    return result


def summarize(items):
    cpu = [m["value"] for item in items for m in item["metrics"] if m["key"] == "cpu_percent" and m["value"] is not None]
    memory = [m["value"] for item in items for m in item["metrics"] if m["key"] == "memory_percent" and m["value"] is not None]
    monitored = sum(any(m["value"] is not None for m in item["metrics"]) for item in items)
    high = sum(any(m["unit"] == "%" and m["value"] is not None and m["value"] >= 80 for m in item["metrics"]) for item in items)
    return {"eligible": len(items), "monitored": monitored, "without_data": len(items) - monitored,
            "cpu_average": sum(cpu) / len(cpu) if cpu else None, "cpu_samples": len(cpu),
            "memory_average": sum(memory) / len(memory) if memory else None, "memory_samples": len(memory),
            "high_utilization": high}
