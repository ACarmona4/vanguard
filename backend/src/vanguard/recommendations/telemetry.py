"""Bounded history: Prometheus returns at most 168 hourly peaks per series."""

import json
import os
import time
from urllib.parse import urlencode
from urllib.request import urlopen

from ..utilization.catalog import IDENTITY_LABELS, identity
from ..utilization.service import MetricsUnavailable
from .policy import DEFAULT_POLICY

HISTORY_METRICS = ("cpu_percent", "filesystem_percent", "network_in_bytes_per_second", "network_out_bytes_per_second")


def query_history(resources, *, now=None, policy=DEFAULT_POLICY):
    if not resources:
        return []
    owners = {identity(resource)[0] for resource in resources}
    if len(owners) != 1 or not next(iter(owners)):
        raise ValueError("Recommendation history requires a single authenticated owner")
    labels = [f"owner_id={json.dumps(owners.pop())}"]
    for label in IDENTITY_LABELS[1:]:
        values = {identity(resource)[IDENTITY_LABELS.index(label)] for resource in resources}
        if len(values) == 1:
            labels.append(f"{label}={json.dumps(values.pop())}")
    # A bucket needs at least six distinct source observations. Repeated scrapes
    # retain the provider timestamp and cannot manufacture historical coverage.
    expressions = []
    for metric in HISTORY_METRICS:
        name = "vanguard_" + metric
        selector = "{" + ",".join([f"__name__={json.dumps(name)}", *labels]) + "}"
        peaks = f"max_over_time({selector}[1h]) and (count_over_time({selector}[1h]) >= 6)"
        # Range functions drop __name__. Restore it per metric before the union,
        # otherwise CPU and network with the same resource labels collide.
        expressions.append(f'label_replace(({peaks}), "__name__", "{name}", "", "")')
    matching = ", ".join(("__name__", *IDENTITY_LABELS))
    query = f" or on ({matching}) ".join(expressions)
    end = int(now if now is not None else time.time()) // 3600 * 3600
    params = {"query": query, "start": end - (policy.history_days * 24 - 1) * 3600,
              "end": end, "step": "3600", "timeout": "8s"}
    base = os.getenv("VANGUARD_PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")
    try:
        with urlopen(base + "/api/v1/query_range?" + urlencode(params), timeout=10) as response:
            body = json.load(response)
        if body.get("status") != "success" or body["data"]["resultType"] != "matrix" or body.get("warnings"):
            raise ValueError("Incomplete metric history")
        return body["data"]["result"]
    except Exception as exc:
        raise MetricsUnavailable("Historical metrics are unavailable; optimization checks are paused.") from exc
