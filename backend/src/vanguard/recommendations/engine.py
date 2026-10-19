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
    "cpu_percent": ("CPU", "performance", "Review the processes or queries using the most CPU."),
    "memory_percent": ("Memory", "performance", "Review memory use and check for leaks."),
    "filesystem_percent": ("Storage", "capacity", "Review data retention and plan more storage."),
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
        "cloud_resource_id": resource["resource_id"],
        "resource_status": resource.get("status"),
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
                    f"{'Critical' if critical else 'High'} {label if label == 'CPU' else label.lower()} usage",
                    f"{label} usage stayed at {minimum:.1f}% or higher for {policy.sustained_minutes} minutes.",
                    [action, "Check peak demand before changing capacity."],
                    metric=metric, points=points, value=minimum, unit="%", threshold=threshold,
                    window=f"{policy.sustained_minutes}m",
                    explanation="The value shown is the lowest reading in this period.")


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
                    "Storage may fill within 24 hours", f"Storage could be full in about {hours:.1f} hours if growth continues at the current rate.",
                    ["Check what is causing storage to grow.", "Increase storage or remove data that is no longer needed after checking backups."],
                    metric="hours_to_full", points=points, value=hours, unit="h", threshold=24, window="24h",
                    explanation="Estimated from storage growth over the last 24 hours.")


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
            actions = ["Review a smaller instance or CPU tier.",
                       "Check memory, network, I/O, CPU credits, availability and licensing before resizing."]
            description = f"CPU usage was usually {p95:.1f}% or lower over the last 7 days."
            value, threshold = p95, policy.low_cpu_p95
        else:
            if peak >= policy.low_storage_peak or max(v for _, v in fresh[metric]) >= policy.low_storage_peak:
                continue
            actions = ["Check data growth and backup retention before reducing storage.",
                       "Check Cloud SQL's minimum size and shrink support. Allow for downtime and lower I/O performance."]
            description = f"Peak storage usage was {peak:.1f}% over the last 7 days."
            raw = resource.get("raw_data") or {}
            capacity = (raw.get("settings") or {}).get("dataDiskSizeGb")
            try:
                capacity = float(capacity)
            except (TypeError, ValueError):
                capacity = None
            if capacity is not None and math.isfinite(capacity) and capacity > 0:
                description += f" Latest usage: approximately {capacity * fresh[metric][-1][1] / 100:.1f} GB of {capacity:g} GB allocated."
            value, threshold = peak, policy.low_storage_peak
        findings.append(_finding(resource, f"{metric}_oversized", "recommendation", "info", "cost",
                                 f"Low {label if label == 'CPU' else label.lower()} usage", description, actions,
                                 metric=metric, points=points, value=value, unit="%", threshold=threshold, window="7d",
                                 explanation=(f"95% of hourly CPU peaks were at or below {p95:.1f}%. Highest peak: {peak:.1f}%." if metric == "cpu_percent"
                                              else "The value shown is the highest hourly storage reading.")
                                             + f" Based on {len(points)} hourly readings over seven days."))
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
                                     "Higher network traffic", "Traffic stayed above its usual weekly range for 15 minutes.",
                                     ["Check the source of the extra traffic and its transfer costs.", "Check the link's bandwidth limit before adding capacity."],
                                     metric=metric, points=sustained, value=minimum, unit="B/s", threshold=threshold,
                                     explanation=f"Traffic stayed at or above {minimum / 1024**2:.1f} MiB/s. The weekly reference rate is {usual / 1024**2:.1f} MiB/s."))
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
                                 title, (f"Message wait time stayed at {minimum:g} seconds or more for 15 minutes." if metric == "oldest_message_seconds"
                                         else f"At least {minimum:g} deliveries per second failed for 15 minutes."),
                                 ["Check consumers, retries and the dead-letter queue.", "Review the queue's delivery target and capacity."],
                                 metric=metric, points=sustained, value=minimum, unit="s" if critical else "messages/s",
                                 threshold=critical if critical and minimum >= critical else warning,
                                 explanation="The value shown is the lowest reading in the last 15 minutes."))
    return findings


def _reporting_accounts(recent, now):
    """Accounts with at least one fresh reading: their connection and permissions work."""
    return {key[0][:3] for key, points in recent.items()
            if points and 0 <= now - max(points) <= FRESH_SECONDS}


def _visibility_suggestions(resource, definitions, fresh, cpu_history, history_available, policy,
                            account_reporting=False):
    findings = []
    # Without traffic CloudWatch sends nothing, so silence only means missing data
    # when the whole account is silent too.
    required = [definition for definition in definitions
                if not definition.optional and not (definition.activity and account_reporting)]
    missing = [definition.label for definition in required if definition.key not in fresh]
    covered = len(required) - len(missing)
    if missing:
        findings.append(_finding(resource, "missing_telemetry", "suggestion", "warning", "observability",
                                 "Missing metrics", "No recent readings for: " + ", ".join(missing) + ".",
                                 ["Check the cloud connection and monitoring permissions.", "Restore metric collection before reviewing capacity."],
                                 metric="metric_coverage", value=covered, unit="metrics", window="20m",
                                 explanation=f"{covered} of {len(required)} required metrics have recent readings."))
    if resource["resource_type"] in VM_TYPES and fresh:
        findings.append(_finding(resource, "capacity_visibility", "suggestion", "info", "observability",
                                 "Add memory and disk metrics", "Memory use and disk space are unavailable for this VM.",
                                 ["Add an agent to collect memory and disk usage.", "Check the VM's network limit before resizing."],
                                 metric="capacity_visibility", window="inventory",
                                 explanation="Review memory, disk and network use before choosing a smaller VM."))
    if "cpu_percent" in fresh and history_available and not _history_ready(cpu_history, policy):
        findings.append(_finding(resource, "insufficient_history", "suggestion", "info", "observability",
                                 "Not enough usage history", "At least seven days of CPU data are needed to review a smaller instance.",
                                 ["Keep collecting metrics for a full week, including scheduled jobs."],
                                 metric="history_coverage", value=len(cpu_history), unit="hours", threshold=math.ceil(policy.history_days * 24 * policy.history_coverage),
                                 points=cpu_history, window="7d", explanation=f"{len(cpu_history)} hourly readings collected. The check needs at least {math.ceil(policy.history_days * 24 * policy.history_coverage)} across the weekly window."))
    return findings


def confirmed_checks(resources, recent_series, history_series, *, now, telemetry_available,
                     history_available, policy=DEFAULT_POLICY):
    """Rules whose absence is meaningful; outages and sparse samples cannot clear them."""
    recent, history = indexed_series(recent_series), indexed_series(history_series)
    checked = set()
    for resource in resources:
        prefix = f"{resource['id']}:"
        seen = resource.get("last_seen_at")
        if isinstance(seen, str):
            seen = datetime.fromisoformat(seen.replace("Z", "+00:00"))
        if (resource["resource_type"] == "ebs_volume" and seen
                and 0 <= now - seen.timestamp() <= 1800
                and isinstance((resource.get("raw_data") or {}).get("Attachments"), list)):
            checked.add(prefix + "unattached_volume")
        if not telemetry_available or (resource.get("status") or "").lower() in INACTIVE:
            continue
        definitions = metrics_for(resource)
        readings = {definition.key: _points(recent, resource, definition.key, now, 1800) for definition in definitions}
        fresh = {key: points for key, points in readings.items() if points and now - points[-1][0] <= FRESH_SECONDS}
        historical = {key: _points(history, resource, key, now, policy.history_days * 86400 + 3600) for key in readings}
        if definitions:
            checked.add(prefix + "missing_telemetry")
        for metric in (*PRESSURE, "oldest_message_seconds", "failed_per_second"):
            if _sustained(readings.get(metric, []), now, policy):
                checked.add(prefix + metric + "_high")
        if history_available:
            if "cpu_percent" in fresh:
                checked.add(prefix + "insufficient_history")
            for metric in ("cpu_percent", "filesystem_percent"):
                if metric in fresh and _history_ready(historical.get(metric, []), policy):
                    checked.add(prefix + metric + "_oversized")
            for metric in ("network_in_bytes_per_second", "network_out_bytes_per_second"):
                baseline = [point for point in historical.get(metric, []) if point[0] < now - 3 * 3600]
                if _sustained(readings.get(metric, []), now, policy) and _history_ready(baseline, policy):
                    checked.add(prefix + metric + "_growth")
            if "filesystem_percent" in fresh:
                end = fresh["filesystem_percent"][-1][0]
                points = [point for point in historical.get("filesystem_percent", []) if end - 86400 <= point[0] <= end]
                if (len(points) >= 18 and points[-1][0] - points[0][0] >= 18 * 3600
                        and all(b[0] - a[0] <= 7200 for a, b in zip(points, points[1:]))):
                    checked.add(prefix + "storage_exhaustion")
    return sorted(checked)


def evaluate(resources, recent_series, history_series, *, now=None, telemetry_available=True,
             history_available=True, policy=DEFAULT_POLICY):
    now = now if now is not None else time.time()
    recent, history = indexed_series(recent_series), indexed_series(history_series)
    findings, eligible, monitored, ready = [], 0, 0, 0
    reporting = _reporting_accounts(recent, now)
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
        findings.extend(_visibility_suggestions(resource, definitions, fresh, cpu_history, history_available, policy,
                                                identity(resource)[:3] in reporting))
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
                                     "Unattached volume", "This volume is not attached to an instance and may still incur storage charges.",
                                     ["Check whether the volume is still needed.", "Confirm ownership and backups before taking a snapshot or deleting it."],
                                     metric="attachments", value=0, unit="attachments", window="inventory",
                                     explanation="No attached instances in the latest inventory."))
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
