"""PaaS acceptance scenarios include negative evidence and automatic resolution."""

import unittest
from datetime import datetime, timedelta, timezone

from test_recommendations import NOW, resource, rules, series
from vanguard.recommendations.engine import confirmed_checks, evaluate
from vanguard.recommendations.schemas import Finding


def item(kind, **extra):
    return resource(resource_type=kind, **extra)


class PaaSRecommendationTests(unittest.TestCase):
    def run_engine(self, item, recent=(), history=(), **extra):
        findings, _ = evaluate([item], recent, history, now=NOW, **extra)
        for finding in findings:
            Finding.model_validate(finding)
        return rules(findings)

    def checked(self, item, recent=(), history=(), **extra):
        return set(confirmed_checks([item], recent, history, now=NOW, telemetry_available=True, history_available=True, **extra))

    def test_function_failures_and_throttling_need_sustained_finite_readings(self):
        function = item("lambda_function", provider="aws", attributes={"timeout_seconds": 10})
        for metric in ("errors_per_second", "throttles_per_second"):
            self.assertIn(metric + "_high", self.run_engine(function, [series(function, metric, [1] * 4)]))
            for values in ([1], [0, 1, 1, 1], [1, "NaN", 1, 1]):
                self.assertNotIn(metric + "_high", self.run_engine(function, [series(function, metric, values)]))
            self.assertNotIn("1:" + metric + "_high", self.checked(function))
            self.assertIn("1:" + metric + "_high", self.checked(function, [series(function, metric, [0] * 4)]))

    def test_timeout_uses_milliseconds_and_configured_limit(self):
        function = item("lambda_function", provider="aws", attributes={"timeout_seconds": 10})
        findings = self.run_engine(function, [series(function, "duration_p95_ms", [8500] * 4)])
        self.assertEqual(findings["function_timeout_risk"]["evidence"]["value"], 85)
        self.assertNotIn("function_timeout_risk", self.run_engine(function, [series(function, "duration_p95_ms", [7000] * 4)]))
        self.assertNotIn("1:function_timeout_risk", self.checked({**function, "attributes": {}}))

    def test_serverless_silence_never_means_idle_billed_capacity(self):
        function = item("lambda_function", provider="aws", attributes={"provisioned_concurrency": 2})
        self.assertNotIn("serverless_idle_capacity", self.run_engine(function))
        recent = [series(function, "invocations_per_second", [0] * 4)]
        history = [series(function, "invocations_per_second", [0] * 168, history=True)]
        self.assertIn("serverless_idle_capacity", self.run_engine(function, recent, history))
        burst = [series(function, "invocations_per_second", [0] * 167 + [1], history=True)]
        self.assertNotIn("serverless_idle_capacity", self.run_engine(function, recent, burst))
        self.assertNotIn("1:serverless_idle_capacity", self.checked(function, recent))
        self.assertNotIn("serverless_idle_capacity", self.run_engine(function, recent, history, history_available=False))
        self.assertIn("1:serverless_idle_capacity", self.checked(function, recent, burst))

    def test_cloud_run_pressure_does_not_generate_vm_rightsizing(self):
        run = item("cloud_run_service", attributes={"min_instances": 1})
        self.assertIn("cpu_percent_high", self.run_engine(run, [series(run, "cpu_percent", [90] * 4)]))
        self.assertNotIn("cpu_percent_oversized", self.run_engine(run, [series(run, "cpu_percent", [5] * 4)], [series(run, "cpu_percent", [5] * 168, history=True)]))

    def test_dynamodb_capacity_uses_matched_historical_capacity_and_blocks_bursts(self):
        table = item("dynamodb_table", provider="aws", attributes={"billing_mode": "PROVISIONED"})
        recent = [series(table, "read_units_per_second", [1] * 4), series(table, "provisioned_read_units", [100] * 4)]
        history = [series(table, "read_units_per_second", [1] * 168, history=True), series(table, "provisioned_read_units", [100] * 168, history=True)]
        self.assertIn("dynamodb_read_capacity", self.run_engine(table, recent, history))
        self.assertNotIn("dynamodb_read_capacity", self.run_engine({**table, "attributes": {"billing_mode": "PAY_PER_REQUEST"}}, recent, history))
        self.assertNotIn("dynamodb_read_capacity", self.run_engine(table, recent, history[:1]))
        throttles = [series(table, "read_throttles_per_second", [1] * 4)]
        self.assertNotIn("dynamodb_read_capacity", self.run_engine(table, recent + throttles, history))
        self.assertNotIn("1:dynamodb_read_capacity", self.checked(table, recent))
        self.assertIn("1:dynamodb_read_capacity", self.checked(table, recent, history))
        history[0] = series(table, "read_units_per_second", [1] * 167 + [90], history=True)
        self.assertNotIn("dynamodb_read_capacity", self.run_engine(table, recent, history))

    def test_long_polling_review_needs_empty_receives_and_resolves_with_fresh_config(self):
        queue = item("sqs_queue", provider="aws", attributes={"long_polling_seconds": 0})
        recent = [series(queue, "empty_receives_per_second", [2] * 4)]
        self.assertIn("sqs_short_polling", self.run_engine(queue, recent))
        configured = {**queue, "attributes": {"long_polling_seconds": 20}}
        self.assertNotIn("sqs_short_polling", self.run_engine(configured, recent))
        self.assertIn("1:sqs_short_polling", self.checked(configured, recent))
        self.assertNotIn("1:sqs_short_polling", self.checked(configured))
        self.assertNotIn("sqs_short_polling", self.run_engine({**queue, "attributes": {"long_polling_seconds": None}}, recent))

    def test_buckets_use_known_lifecycle_and_tiering_configuration_only(self):
        bucket = item("s3_bucket", provider="aws", attributes={"lifecycle_rules": 0, "intelligent_tiering": False})
        self.assertIn("bucket_lifecycle", self.run_engine(bucket, telemetry_available=False))
        for attributes in ({"lifecycle_rules": None, "intelligent_tiering": False}, {"lifecycle_rules": 0, "intelligent_tiering": None}, {"lifecycle_rules": 1, "intelligent_tiering": False}, {"lifecycle_rules": 0, "intelligent_tiering": True}):
            self.assertNotIn("bucket_lifecycle", self.run_engine({**bucket, "attributes": attributes}))
        updated = {**bucket, "attributes": {"lifecycle_rules": 1, "intelligent_tiering": False}}
        self.assertIn("1:bucket_lifecycle", self.checked(updated))
        self.assertNotIn("1:bucket_lifecycle", self.checked({**updated, "last_seen_at": datetime.fromtimestamp(NOW-7200, timezone.utc)}))

    def test_manual_snapshots_use_age_and_never_claim_full_storage_as_savings(self):
        snapshot = item("rds_snapshot", provider="aws", attributes={"created_at": datetime.fromtimestamp(NOW, timezone.utc)-timedelta(days=40)})
        finding = self.run_engine(snapshot)["snapshot_retention"]
        self.assertEqual(finding["evidence"]["value"], 40)
        self.assertNotIn("cost", finding)
        self.assertNotIn("snapshot_retention", self.run_engine({**snapshot, "attributes": {"created_at": "bad"}}))

    def test_bigquery_rules_do_not_treat_unreadable_jobs_as_unused(self):
        table = item("bigquery_table", attributes={"table_type": "TABLE", "size_bytes": 2*1024**3,
                     "partitioned": False, "queries_7d": 5, "expires_at": None,
                     "created_at": datetime.fromtimestamp(NOW-30*86400, timezone.utc).isoformat()})
        self.assertIn("bigquery_partitioning", self.run_engine(table))
        self.assertNotIn("bigquery_retention", self.run_engine(table))
        unused = {**table, "attributes": {**table["attributes"], "queries_7d": 0}}
        self.assertIn("bigquery_retention", self.run_engine(unused))
        unknown = {**table, "attributes": {**table["attributes"], "queries_7d": None}}
        self.assertNotIn("bigquery_retention", self.run_engine(unknown))
        self.assertNotIn("1:bigquery_retention", self.checked(unknown))
        partitioned = {**table, "attributes": {**table["attributes"], "partitioned": True, "require_partition_filter": False}}
        self.assertIn("bigquery_partition_filter", self.run_engine(partitioned))
        fixed = {**partitioned, "attributes": {**partitioned["attributes"], "require_partition_filter": True}}
        self.assertIn("1:bigquery_partition_filter", self.checked(fixed))

    def test_database_backups_and_topic_subscriptions_distinguish_unknown(self):
        db = item("rds_instance", provider="aws", attributes={"backup_retention_days": 0})
        self.assertIn("database_backups", self.run_engine(db))
        self.assertNotIn("database_backups", self.run_engine({**db, "attributes": {}}))
        for kind, provider in (("sns_topic", "aws"), ("pubsub_topic", "gcp")):
            topic = item(kind, provider=provider, attributes={"subscriptions": 0})
            self.assertIn("topic_without_subscribers", self.run_engine(topic))
            self.assertNotIn("1:topic_without_subscribers", self.checked({**topic, "attributes": {"subscriptions": None}}))

    def test_rds_storage_pressure_has_no_unsupported_shrink_recommendation(self):
        db = item("rds_instance", provider="aws", attributes={"allocated_storage_gb": 100})
        low = self.run_engine(db, [series(db, "filesystem_percent", [5]*4)], [series(db, "filesystem_percent", [5]*168, history=True)])
        self.assertNotIn("filesystem_percent_oversized", low)
        self.assertIn("filesystem_percent_high", self.run_engine(db, [series(db, "filesystem_percent", [95]*4)]))

    def test_metadata_cannot_prove_cloud_monitoring_connection_works(self):
        function = item("lambda_function", provider="aws", attributes={"provisioned_concurrency": 2})
        readings = [series(function, "provisioned_concurrency", [2] * 4)]
        self.assertIn("missing_telemetry", self.run_engine(function, readings))

    def test_gen2_backing_run_service_does_not_duplicate_findings(self):
        function = item("cloud_function", attributes={"generation": "GEN_2", "run_service": "api"})
        run = item("cloud_run_service", id=2, name="api")
        recent = [series(resource, "errors_per_second", [1] * 4) for resource in (function, run)]
        findings, _ = evaluate([function, run], recent, [], now=NOW)
        errors = [finding for finding in findings if finding["rule"] == "errors_per_second_high"]
        self.assertEqual([finding["resource_type"] for finding in errors], ["cloud_function"])
