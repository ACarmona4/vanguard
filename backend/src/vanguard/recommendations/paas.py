"""PaaS checks share detection and verification: unknown evidence never clears a finding."""

from datetime import datetime

from .engine import _finding, _history_ready, _recent_inventory, _sustained


def _age(value, now):
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        return max(0, (now - observed.timestamp()) / 86400) if observed and observed.timestamp() <= now else None
    except (TypeError, ValueError, AttributeError):
        return None


def inventory_checks(resource, now, policy):
    checks = {}
    if not _recent_inventory(resource, now):
        return checks
    attributes = resource.get("attributes") or {}
    kind = resource["resource_type"]

    def add(rule, condition, title, description, actions, *, value=None, unit="", category="cost", metric="configuration"):
        checks[rule] = _finding(resource, rule, "recommendation" if category == "cost" else "suggestion", "info", category,
                                title, description, actions, metric=metric, value=value, unit=unit, window="inventory",
                                explanation="Observed in the latest inventory. Review ownership, retention and workload requirements before changing configuration.") if condition else None

    if kind in {"s3_bucket", "storage_bucket"} and attributes.get("lifecycle_rules") is not None:
        managed = attributes.get("intelligent_tiering") if kind == "s3_bucket" else attributes.get("autoclass")
        # A denied detail call is unknown, not proof of absent policies.
        if managed is not None:
            add("bucket_lifecycle", attributes["lifecycle_rules"] == 0 and not managed,
                "Review object storage lifecycle", "No lifecycle rules or automatic tiering were observed for this bucket.",
                ["Review object age, access patterns, legal holds and retention requirements.",
                 "Consider tier transitions and expired-version cleanup; account for retrieval and early deletion charges."],
                value=attributes["lifecycle_rules"], unit="rules")
    if kind == "rds_snapshot":
        age = _age(attributes.get("created_at"), now)
        if age is not None:
            add("snapshot_retention", age >= policy.snapshot_review_days,
                "Review old manual snapshot", f"This manual snapshot is {age:.0f} days old and has no automatic expiry.",
                ["Confirm the backup owner and required recovery window.", "Remove only snapshots no longer needed after verifying another recovery point."],
                value=age, unit="days")
    if kind in {"rds_instance", "rds_cluster", "cloudsql_instance"}:
        retention = attributes.get("backup_retention_days")
        backups = attributes.get("backups_enabled")
        if retention is not None or backups is not None:
            add("database_backups", retention == 0 or backups is False,
                "Review database backup coverage", "Automatic backups are disabled in the latest inventory.",
                ["Confirm the recovery point and recovery time requirements.", "Enable an appropriate backup policy and test restoration."], category="reliability")
    if kind in {"sns_topic", "pubsub_topic"} and attributes.get("subscriptions") is not None:
        add("topic_without_subscribers", attributes["subscriptions"] == 0,
            "Topic has no subscriptions", "The topic has no confirmed subscriptions; published messages may have no consumers.",
            ["Confirm whether publishers still use this topic.", "Check pending subscriptions, retention and downstream consumers before removing it."],
            value=attributes["subscriptions"], unit="subscriptions", category="reliability")
    if kind == "bigquery_table" and attributes.get("table_type") == "TABLE":
        size, queries = attributes.get("size_bytes"), attributes.get("queries_7d")
        if size is not None and attributes.get("partitioned") is not None and queries is not None:
            add("bigquery_partitioning", size >= policy.bigquery_large_table_bytes and not attributes["partitioned"] and queries > 0,
                "Review BigQuery table partitioning", "A large queried table has no partitioning policy.",
                ["Inspect query predicates and bytes scanned before choosing partitioning or clustering.",
                 "Measure query cost after the change; query bytes can be shared by several referenced tables."],
                value=size, unit="B")
        if attributes.get("partitioned") and attributes.get("require_partition_filter") is not None:
            add("bigquery_partition_filter", not attributes["require_partition_filter"],
                "Require a BigQuery partition filter", "Queries can scan this partitioned table without a partition filter.",
                ["Review query compatibility before requiring a partition filter.", "Measure bytes scanned and update clients before enforcing the policy."])
        age = _age(attributes.get("created_at"), now)
        if queries is not None and age is not None and "expires_at" in attributes:
            add("bigquery_retention", queries == 0 and age >= policy.history_days and not attributes["expires_at"] and (size or 0) > 0,
                "Review unused BigQuery table retention", "No queries referencing this table were observed in seven days and no table expiry is configured.",
                ["Check writes, external readers, scheduled jobs, legal retention and dataset defaults.", "Consider an expiry policy only after the data owner confirms retention requirements."],
                value=queries, unit="queries")
    return checks


def metric_checks(resource, readings, historical, *, now, history_available, policy):
    checks = {}
    attributes = resource.get("attributes") or {}
    kind = resource["resource_type"]
    for metric, title in (("errors_per_second", "Service executions are failing"),
                          ("throttles_per_second", "Service requests are throttled"),
                          ("read_throttles_per_second", "Database reads are throttled"),
                          ("write_throttles_per_second", "Database writes are throttled")):
        points = _sustained(readings.get(metric, []), now, policy)
        if points:
            minimum = min(value for _, value in points)
            checks[metric + "_high"] = _finding(
                resource, metric + "_high", "alert", "warning", "reliability", title,
                f"The failure rate remained at least {minimum:g} requests/s for {policy.sustained_minutes} minutes.",
                ["Inspect execution logs, retries and dead-letter destinations.", "Check concurrency, quotas, downstream capacity and recent releases."],
                metric=metric, points=points, value=minimum, unit="requests/s", threshold=0,
                explanation="Every reading in the sustained window was positive.") if minimum > 0 else None
    if not _recent_inventory(resource, now):
        return checks
    timeout = attributes.get("timeout_seconds")
    duration = _sustained(readings.get("duration_p95_ms", []), now, policy)
    if kind in {"lambda_function", "cloud_function"} and timeout and duration:
        value = min(v for _, v in duration) / (float(timeout) * 1000) * 100
        checks["function_timeout_risk"] = _finding(
            resource, "function_timeout_risk", "recommendation", "warning", "performance",
            "Execution duration approaches the timeout", "The p95 execution duration is close to the configured timeout.",
            ["Profile slow code and downstream dependencies.", "Review retry behavior and timeout limits before changing memory or concurrency."],
            metric="duration_p95_ms", points=duration, value=value, unit="%", threshold=policy.warning_percent,
            explanation="Lowest p95 duration in the sustained window divided by the configured timeout.") if value >= policy.warning_percent else None
    if kind == "sqs_queue" and attributes.get("long_polling_seconds") is not None:
        points = _sustained(readings.get("empty_receives_per_second", []), now, policy)
        if points:
            value = min(v for _, v in points)
            checks["sqs_short_polling"] = _finding(
                resource, "sqs_short_polling", "recommendation", "info", "cost", "Reduce empty queue polling",
                "Empty receive requests were sustained while the queue default long polling is disabled.",
                ["Check whether consumers override WaitTimeSeconds.", "Consider long polling and validate consumer latency and HTTP timeouts."],
                metric="empty_receives_per_second", points=points, value=value, unit="requests/s", threshold=0,
                explanation="Positive empty receives and a zero queue-level long polling wait; client overrides may differ.") if value > 0 and attributes["long_polling_seconds"] == 0 else None
    if not history_available:
        return checks
    if kind == "dynamodb_table" and attributes.get("billing_mode") == "PROVISIONED":
        for direction in ("read", "write"):
            metric = f"{direction}_units_per_second"
            capacity_metric = f"provisioned_{direction}_units"
            usage, capacity = historical.get(metric, []), historical.get(capacity_metric, [])
            current = _sustained(readings.get(metric, []), now, policy)
            current_capacity = dict(_sustained(readings.get(capacity_metric, []), now, policy))
            caps = dict(capacity)
            ratios = [(stamp, value / caps[stamp] * 100) for stamp, value in usage if caps.get(stamp, 0) > 0]
            recent_ratios = [value / current_capacity[stamp] * 100 for stamp, value in current if current_capacity.get(stamp, 0) > 0]
            # Hourly capacity uses minima, while consumption uses maxima: never understate use.
            if _history_ready(ratios, policy) and len(recent_ratios) >= 4:
                peak = max([v for _, v in ratios] + recent_ratios)
                throttled = any(value > 0 for key in ("read_throttles_per_second", "write_throttles_per_second")
                                for _, value in readings.get(key, []) + historical.get(key, []))
                rule = f"dynamodb_{direction}_capacity"
                checks[rule] = _finding(
                    resource, rule, "recommendation", "info", "cost", f"Review provisioned {direction} capacity",
                    f"Peak observed {direction} capacity usage stayed below {policy.low_capacity_percent:g}% over seven days.",
                    ["Review auto scaling targets, reserved capacity, indexes, scheduled peaks and burst credits.",
                     "Compare provisioned and on-demand pricing before changing billing mode."],
                    metric=metric, points=ratios, value=peak, unit="%", threshold=policy.low_capacity_percent, window="7d",
                    explanation="Maximum consumed units divided by minimum provisioned units in each hourly bucket; no observed throttling.") if peak < policy.low_capacity_percent and not throttled else None
    if kind in {"lambda_function", "cloud_run_service", "cloud_function"}:
        metric = "invocations_per_second" if kind == "lambda_function" or (kind == "cloud_function" and attributes.get("generation") != "GEN_2") else "requests_per_second"
        points = historical.get(metric, [])
        current = _sustained(readings.get(metric, []), now, policy)
        floor = attributes.get("provisioned_concurrency") if kind == "lambda_function" else attributes.get("min_instances")
        if floor is not None and _history_ready(points, policy) and current:
            peak = max(v for _, v in points + current)
            checks["serverless_idle_capacity"] = _finding(
                resource, "serverless_idle_capacity", "recommendation", "info", "cost", "Review idle serverless capacity",
                "A nonzero provisioned capacity floor has seven days of explicitly observed zero traffic.",
                ["Confirm scheduled jobs, cold-start targets and availability requirements.", "Review reducing provisioned concurrency or minimum instances and measure the result."],
                metric=metric, points=points, value=peak, unit="requests/s", threshold=0, window="7d",
                explanation="Only explicit zero observations count; missing traffic metrics are never treated as zero.") if floor > 0 and peak == 0 else None
    return checks
