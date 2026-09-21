"""OTLP/HTTP JSON gauges preserve provider timestamps (not collection time)."""

import json
import math
from urllib.request import Request, urlopen

from .catalog import IDENTITY_LABELS, identity


def payload(samples):
    resources = {}
    for resource, metric, timestamp, value in samples:
        if not math.isfinite(value):
            continue
        key = identity(resource)
        item = resources.setdefault(key, {
            "resource": {"attributes": [
                {"key": label, "value": {"stringValue": value}}
                for label, value in zip(IDENTITY_LABELS, key)
            ]},
            "scopeMetrics": [{"scope": {"name": "vanguard.cloud"}, "metrics": []}],
        })
        item["scopeMetrics"][0]["metrics"].append({
            "name": f"vanguard_{metric.key}",
            "description": metric.label,
            "gauge": {"dataPoints": [{"timeUnixNano": str(int(timestamp * 1_000_000_000)),
                                      "asDouble": value}]},
        })
    return {"resourceMetrics": list(resources.values())}


def export(samples, endpoint):
    if not samples:
        return
    request = Request(endpoint, data=json.dumps(payload(samples)).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=15) as response:
        body = json.loads(response.read() or b"{}")
        if body.get("partialSuccess", {}).get("rejectedDataPoints", 0) not in (0, "0"):
            raise RuntimeError("El Collector rechazó muestras OTLP")
