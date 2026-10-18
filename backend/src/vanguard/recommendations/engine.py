"""Pure rules: deterministic findings, explicit evidence, no cloud mutations."""

import math
import time
from datetime import datetime, timezone

from ..utilization.catalog import identity, metrics_for
from ..utilization.service import FRESH_SECONDS, indexed_series
from .policy import DEFAULT_POLICY

VM_TYPES = {"ec2_instance", "compute_instance"}
INACTIVE = {"stopped", "stopping", "terminated", "terminating", "suspended", "suspending", "deleting"}
PRESSURE = {
    "cpu_percent": ("CPU", "performance", "Profile the busiest processes and queries; check latency and CPU credits before scaling."),
    "memory_percent": ("Memory", "performance", "Check working set, cache behavior and memory leaks; evaluate more memory if pressure persists."),
    "filesystem_percent": ("Storage", "capacity", "Review retention, reclaim disposable data and plan additional storage before capacity runs out."),
}


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp is not None else None


def _points(index, resource, metric, now, seconds):
    return sorted((stamp, value) for stamp, value in index.get((identity(resource), metric), {}).items()
                  if now - seconds <= stamp <= now and value >= 0
                  and (not metric.endswith("_percent") or value <= 100))


def _sustained(points, now, policy):
    if not points or now - points[-1][0] > FRESH_SECONDS:
        return []
    end = points[-1][0]
    selected = [point for point in points if point[0] >= end - policy.sustained_minutes * 60]
    if (len(selected) < 4 or selected[-1][0] - selected[0][0] < policy.sustained_minutes * 60
            or any(b[0] - a[0] > policy.max_gap_seconds for a, b in zip(selected, selected[1:]))):
        return []
    return selected


def _history_ready(points, policy):
    return (len(points) >= math.ceil(policy.history_days * 24 * policy.history_coverage)
            and points[-1][0] - points[0][0] >= (policy.history_days - 1) * 86400)


def _percentile(values, percentile):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]


def _finding(resource, rule, kind, severity, category, title, description, actions,
             *, metric, points=(), value=None, unit="", threshold=None, window="15m", explanation):
    return {
        "id": f"{resource['id']}:{rule}", "rule": rule, "kind": kind, "severity": severity,
        "category": category, "title": title, "description": description, "actions": actions,
        "resource_id": resource["id"], "resource_name": resource.get("name") or resource["resource_id"],
        **{key: resource.get(key) for key in ("provider", "resource_type", "scope_id", "region")},
        "evidence": {"metric": metric, "value": value, "unit": unit, "threshold": threshold,
                     "samples": len(points), "window": window,
                     "observed_at": _iso(points[-1][0]) if points else None, "explanation": explanation},
    }


def _pressure(resource, metric, points, policy):
    label, category, action = PRESSURE[metric]
    minimum = min(value for _, value in points)
    if minimum < policy.warning_percent:
        return None
    critical = minimum >= policy.critical_percent
    threshold = policy.critical_percent if critical else policy.warning_percent
    return _finding(resource, f"{metric}_high", "alert", "critical" if critical else "warning", category,
                    f"{label} utilization is {'critical' if critical else 'high'}",
                    f"Every observation remained at or above {threshold:g}% for {policy.sustained_minutes} minutes.",
                    [action, "Validate workload peaks and availability requirements before changing capacity."],
                    metric=metric, points=points, value=minimum, unit="%", threshold=threshold,
                    window=f"{policy.sustained_minutes}m",
                    explanation="Minimum across the sustained window ending at the last observation; isolated spikes do not trigger this alert.")


def _storage_forecast(resource, current, history):
    # Hourly maxima over the latest 24h; require coverage and a convincing trend.
    points = [point for point in history if current[0] - 86400 <= point[0] <= current[0]]
    if current[1] < 70 or current[1] >= 100 or len(points) < 18:
        return None
    if points[-1][0] - points[0][0] < 18 * 3600 or any(b[0] - a[0] > 7200 for a, b in zip(points, points[1:])):
        return None
    xs = [(stamp - points[0][0]) / 3600 for stamp, _ in points]
    ys = [value for _, value in points]
    x_mean, y_mean = sum(xs) / len(xs), sum(ys) / len(ys)
    variance = sum((x - x_mean) ** 2 for x in xs)
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / variance
    total = sum((y - y_mean) ** 2 for y in ys)
    residual = sum((y - (y_mean + slope * (x - x_mean))) ** 2 for x, y in zip(xs, ys))
    if slope <= 0 or total == 0 or 1 - residual / total < 0.7:
        return None
    hours = (100 - current[1]) / slope
    if hours > 24:
        return None
    return _finding(resource, "storage_exhaustion", "recommendation", "warning", "capacity",
                    "Storage may fill within 24 hours", f"At the observed trend, remaining space may run out in about {hours:.1f} hours.",
                    ["Check recent data growth and retention jobs.", "Prepare a capacity increase or safe cleanup; recheck the trend after changes."],
                    metric="hours_to_full", points=points, value=hours, unit="h", threshold=24, window="24h",
                    explanation="Linear extrapolation of hourly storage peaks (R² ≥ 0.7); an estimate, not a guarantee.")


def _cost_reviews(resource, fresh, historical, policy):
    findings = []
    # Conservative candidates for cost review: hourly peaks preserve bursts.
    for metric, label in (("cpu_percent", "CPU"), ("filesystem_percent", "Storage")):
        points = historical.get(metric, [])
        if metric not in fresh or not _history_ready(points, policy):
            continue
        values = [value for _, value in points]
        p95, peak = _percentile(values, 0.95), max(values)
        other_pressure = any(any(value >= policy.warning_percent for _, value in data)
                             for key, data in fresh.items() if key in PRESSURE and key != metric)
        if metric == "cpu_percent":
            # Never recommend a smaller instance while another known resource is pressured.
            if p95 >= policy.low_cpu_p95 or peak >= policy.low_cpu_peak or other_pressure or max(v for _, v in fresh[metric]) >= policy.low_cpu_peak:
                continue
            actions = ["Compare a smaller instance or CPU tier against representative workload peaks.",
                       "Validate memory, network, disk I/O, CPU credits, redundancy and licensing before resizing."]
            description = f"P95 of hourly CPU peaks is {p95:.1f}% and the maximum is {peak:.1f}% over seven days. Capacity may be oversized."
            value, threshold = p95, policy.low_cpu_p95
        else:
            if peak >= policy.low_storage_peak or max(v for _, v in fresh[metric]) >= policy.low_storage_peak:
                continue
            actions = ["Compare provisioned capacity with data growth, retention and required free space.",
                       "For Cloud SQL, check storage-shrink eligibility and the provider's minimum target size; shrinking requires downtime and can reduce I/O performance. Consider migration when appropriate."]
            description = f"Storage never exceeded {peak:.1f}% in the observed week; at least {100 - peak:.1f}% of capacity stayed unused."
            raw = resource.get("raw_data") or {}
            capacity = (raw.get("settings") or {}).get("dataDiskSizeGb")
            try:
                capacity = float(capacity)
            except (TypeError, ValueError):
                capacity = None
            if capacity is not None and math.isfinite(capacity) and capacity > 0:
                description += f" Provisioned: {capacity:g} GB; latest estimated usage: {capacity * fresh[metric][-1][1] / 100:.1f} GB."
            value, threshold = peak, policy.low_storage_peak
        findings.append(_finding(resource, f"{metric}_oversized", "recommendation", "info", "cost",
                                 f"Review overprovisioned {label.lower()} capacity", description, actions,
                                 metric=metric, points=points, value=value, unit="%", threshold=threshold, window="7d",
                                 explanation="At least 80% of hourly buckets across a weekly cycle; each bucket contains ≥6 source samples. Savings depend on provider pricing and workload constraints."))
    return findings


def _network_changes(resource, readings, historical, now, policy):
    findings = []
    for metric in ("network_in_bytes_per_second", "network_out_bytes_per_second"):
        sustained = _sustained(readings.get(metric, []), now, policy)
        baseline = [point for point in historical.get(metric, []) if point[0] < now - 3 * 3600]
        if not sustained or not _history_ready(baseline, policy):
            continue
        usual = _percentile([value for _, value in baseline], 0.95)
        threshold = max(policy.network_min_bytes_per_second, usual * policy.network_growth_factor)
        minimum = min(value for _, value in sustained)
        if minimum >= threshold:
            findings.append(_finding(resource, metric + "_growth", "recommendation", "warning", "performance",
                                     "Network traffic is above its usual range", "Sustained traffic exceeds the weekly baseline. This indicates a traffic change, not proven link saturation.",
                                     ["Check traffic sources, replication, backups and egress costs.", "Compare traffic with the provider's actual bandwidth limit before increasing capacity."],
                                     metric=metric, points=sustained, value=minimum, unit="B/s", threshold=threshold,
                                     explanation="Minimum over 15m ≥3× P95 of historical hourly peaks (excluding the last 3h), with a 1 MiB/s floor."))
    return findings


def _delivery_alerts(resource, readings, now, policy):
    findings = []
    for metric, warning, critical, title in (
        ("oldest_message_seconds", policy.queue_warning_seconds, policy.queue_critical_seconds, "Messages are waiting too long"),
        ("failed_per_second", 0, None, "Message deliveries are failing"),
    ):
        sustained = _sustained(readings.get(metric, []), now, policy)
        if not sustained:
            continue
        minimum = min(value for _, value in sustained)
        if minimum < warning or (warning == 0 and minimum == 0):
            continue
        findings.append(_finding(resource, metric + "_high", "alert", "critical" if critical and minimum >= critical else "warning", "reliability",
                                 title, "The condition persisted across the observation window.",
                                 ["Check consumers, delivery errors, retry policies and dead-letter queues.", "Adjust the threshold to the queue's processing SLO."],
                                 metric=metric, points=sustained, value=minimum, unit="s" if critical else "messages/s",
                                 threshold=critical if critical and minimum >= critical else warning,
                                 explanation="Minimum across 15m of recent samples; queue age thresholds are product defaults, not universal SLOs."))
    return findings


def _visibility_suggestions(resource, definitions, fresh, cpu_history, history_available, policy):
    findings = []
    missing = [definition.label for definition in definitions if definition.key not in fresh]
    if missing:
        findings.append(_finding(resource, "missing_telemetry", "suggestion", "warning", "observability",
                                 "Telemetry is missing or stale", "Unavailable signals: " + ", ".join(missing) + ".",
                                 ["Check the cloud connection, monitoring permissions and collection status.", "Do not interpret missing observations as zero utilization."],
                                 metric="metric_coverage", value=len(fresh), unit="metrics", window="20m",
                                 explanation=f"{len(fresh)} of {len(definitions)} supported metrics have a recent observation."))
    if resource["resource_type"] in VM_TYPES and fresh:
        findings.append(_finding(resource, "capacity_visibility", "suggestion", "info", "observability",
                                 "Complete capacity visibility", "Native VM metrics do not include guest memory or filesystem occupancy. Network throughput has no known capacity denominator.",
                                 ["Review guest memory and filesystem telemetry with an agent before reducing the machine size.", "Confirm the machine's bandwidth limit to assess network saturation; disk I/O does not measure occupied space."],
                                 metric="capacity_visibility", window="inventory",
                                 explanation="A low CPU reading alone cannot prove that the entire machine is oversized."))
    if "cpu_percent" in fresh and history_available and not _history_ready(cpu_history, policy):
        findings.append(_finding(resource, "insufficient_history", "suggestion", "info", "observability",
                                 "Collect a representative usage history", "Cost optimization needs a weekly cycle before suggesting a smaller CPU tier.",
                                 ["Keep telemetry running for at least seven days and include scheduled workload peaks."],
                                 metric="history_coverage", value=len(cpu_history), unit="hours", threshold=math.ceil(policy.history_days * 24 * policy.history_coverage),
                                 points=cpu_history, window="7d", explanation="At least 80% hourly coverage spanning six days is required; missing data never counts as idle time."))
    return findings


def evaluate(resources, recent_series, history_series, *, now=None, telemetry_available=True,
             history_available=True, policy=DEFAULT_POLICY):
    now = now if now is not None else time.time()
    recent, history = indexed_series(recent_series), indexed_series(history_series)
    findings, eligible, monitored, ready = [], 0, 0, 0
    for resource in resources:
        if (resource.get("status") or "").lower() in INACTIVE:
            continue
        definitions = metrics_for(resource)
        if not definitions:
            continue
        eligible += 1
        readings = {definition.key: _points(recent, resource, definition.key, now, 1800) for definition in definitions}
        fresh = {key: points for key, points in readings.items() if points and now - points[-1][0] <= FRESH_SECONDS}
        if fresh:
            monitored += 1
        historical = {key: _points(history, resource, key, now, policy.history_days * 86400 + 3600) for key in readings}
        cpu_history = historical.get("cpu_percent", [])
        if history_available and _history_ready(cpu_history, policy):
            ready += 1
        if not telemetry_available:
            continue
        findings.extend(_visibility_suggestions(resource, definitions, fresh, cpu_history, history_available, policy))
        for metric in PRESSURE:
            sustained = _sustained(readings.get(metric, []), now, policy)
            if sustained:
                finding = _pressure(resource, metric, sustained, policy)
                if finding:
                    findings.append(finding)
        storage = fresh.get("filesystem_percent")
        if storage and history_available:
            forecast = _storage_forecast(resource, storage[-1], historical.get("filesystem_percent", []))
            if forecast:
                findings.append(forecast)
        findings.extend(_delivery_alerts(resource, readings, now, policy))
        if history_available:
            findings.extend(_cost_reviews(resource, fresh, historical, policy))
            findings.extend(_network_changes(resource, readings, historical, now, policy))
    # A detached EBS volume can still contain valuable backups; this is a review.
    for resource in resources:
        seen = resource.get("last_seen_at")
        if isinstance(seen, str):
            seen = datetime.fromisoformat(seen.replace("Z", "+00:00"))
        if (resource["resource_type"] == "ebs_volume" and resource.get("status") == "available"
                and (resource.get("raw_data") or {}).get("Attachments") == []
                and seen is not None and 0 <= now - seen.timestamp() <= 1800):
            findings.append(_finding(resource, "unattached_volume", "recommendation", "info", "cost",
                                     "Review an unattached storage volume", "This EBS volume is available and has no attachments, but provisioned storage may still incur charges.",
                                     ["Confirm ownership, backup policies and any planned reattachment.", "Consider a snapshot and removal only after confirming the data is no longer needed."],
                                     metric="attachments", value=0, unit="attachments", window="inventory",
                                     explanation="Based on a fresh inventory snapshot; no inference about filesystem usage or data value."))
    findings.sort(key=lambda item: ({"alert": 0, "recommendation": 1, "suggestion": 2}[item["kind"]],
                                    {"critical": 0, "warning": 1, "info": 2}[item["severity"]], item["resource_id"], item["rule"]))
    summary = {"resources": len(resources), "eligible": eligible, "monitored": monitored,
               "without_data": eligible - monitored, "history_ready": ready,
               "affected_resources": len({item["resource_id"] for item in findings})}
    for key, kind in (("alerts", "alert"), ("recommendations", "recommendation"), ("suggestions", "suggestion")):
        summary[key] = sum(item["kind"] == kind for item in findings)
    for severity in ("critical", "warning", "info"):
        summary[severity] = sum(item["severity"] == severity for item in findings)
    summary["cost_opportunities"] = sum(item["category"] == "cost" for item in findings)
    return findings, summary
