"""Behavioral checks for cost and capacity decisions, including unsafe inputs."""

import json
import unittest
from datetime import datetime, timezone
from typing import Annotated
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from vanguard.recommendations.analysis import evaluate_inventory
from vanguard.recommendations.api import create_router
from vanguard.recommendations.engine import confirmed_checks, evaluate
from vanguard.recommendations.schemas import (
    Finding,
)
from vanguard.recommendations.telemetry import query_history
from vanguard.utilization.catalog import IDENTITY_LABELS
from vanguard.utilization.service import MetricsUnavailable

NOW = 1_800_000_000 // 3600 * 3600


def resource(**overrides):
    return {"id": 1, "owner_id": "owner-a", "provider": "gcp", "scope_id": "project-a",
            "region": "us-central1", "resource_type": "cloudsql_instance", "resource_id": "db-1",
            "name": "orders", "status": "RUNNABLE", "attributes": {"database_version": "POSTGRES_15"},
            "raw_data": {"settings": {"dataDiskSizeGb": "100"}},
            "last_seen_at": datetime.fromtimestamp(NOW, timezone.utc), **overrides}


def series(item, key, values, *, stamps=None, history=False):
    if stamps is None:
        stamps = [NOW - (len(values) - index - 1) * (3600 if history else 300) for index in range(len(values))]
    return {"metric": {**{label: str(item.get(label) or "") for label in IDENTITY_LABELS},
                       "__name__": "vanguard_" + key},
            "values": [[stamp, str(value)] for stamp, value in zip(stamps, values)]}


def rules(findings):
    return {item["rule"]: item for item in findings}


class RecommendationEngineTests(unittest.TestCase):
    def setUp(self):
        self.item = resource()

    def run_engine(self, recent=(), history=(), **kwargs):
        findings, summary = evaluate([self.item], recent, history, now=NOW, **kwargs)
        for finding in findings:
            Finding.model_validate(finding)
        return rules(findings), summary

    def test_sustained_cpu_pressure_warns_and_escalates_without_duplicates(self):
        for value, severity in ((80, "warning"), (90, "critical"), (100, "critical")):
            with self.subTest(value=value):
                findings, summary = self.run_engine([series(self.item, "cpu_percent", [value] * 4)])
                self.assertEqual(findings["cpu_percent_high"]["severity"], severity)
                self.assertEqual(summary["alerts"], 1)
                self.assertEqual(findings["cpu_percent_high"]["evidence"]["samples"], 4)

    def test_single_spikes_and_short_windows_do_not_alert(self):
        for values in ([95], [95] * 3, [10, 95, 95, 95], [95, 95, 95, 10]):
            with self.subTest(values=values):
                findings, _ = self.run_engine([series(self.item, "cpu_percent", values)])
                self.assertNotIn("cpu_percent_high", findings)

    def test_gaps_staleness_invalid_and_future_samples_never_alert(self):
        cases = [([NOW - 1800, NOW - 600, NOW - 300, NOW], [95] * 4),
                 ([NOW - 2400, NOW - 2100, NOW - 1800, NOW - 1500], [95] * 4),
                 ([NOW - 900, NOW - 600, NOW - 300, NOW], [95, "NaN", 95, 95]),
                 ([NOW - 900, NOW - 600, NOW - 300, NOW], [101] * 4),
                 ([NOW + 100, NOW + 400, NOW + 700, NOW + 1000], [95] * 4)]
        for stamps, values in cases:
            with self.subTest(stamps=stamps, values=values):
                findings, _ = self.run_engine([series(self.item, "cpu_percent", values, stamps=stamps)])
                self.assertNotIn("cpu_percent_high", findings)

    def test_seven_percent_cpu_produces_cost_review_after_weekly_history(self):
        findings, summary = self.run_engine([series(self.item, "cpu_percent", [7] * 4)],
                                            [series(self.item, "cpu_percent", [7] * 168, history=True)])
        self.assertIn("cpu_percent_oversized", findings)
        self.assertEqual(summary["cost_opportunities"], 1)
        self.assertEqual(summary["history_ready"], 1)
        self.assertEqual(findings["cpu_percent_oversized"]["evidence"]["value"], 7)

    def test_100gb_database_using_5gb_produces_storage_cost_review(self):
        findings, _ = self.run_engine([series(self.item, "filesystem_percent", [5] * 4)],
                                      [series(self.item, "filesystem_percent", [5] * 168, history=True)])
        finding = findings["filesystem_percent_oversized"]
        self.assertIn("100 GB", finding["description"])
        self.assertIn("5.0 GB", finding["description"])
        self.assertIn("minimum size", finding["actions"][1])
        self.assertIn("downtime", finding["actions"][1])

    def test_oversized_storage_is_not_recommended_when_a_weekly_peak_needs_it(self):
        findings, _ = self.run_engine([series(self.item, "filesystem_percent", [5] * 4)],
                                      [series(self.item, "filesystem_percent", [5] * 167 + [75], history=True)])
        self.assertNotIn("filesystem_percent_oversized", findings)

    def test_cpu_cost_review_is_suppressed_by_recent_spikes_or_memory_pressure(self):
        history = [series(self.item, "cpu_percent", [7] * 168, history=True)]
        for recent in ([series(self.item, "cpu_percent", [7, 70, 7, 7])],
                       [series(self.item, "cpu_percent", [7] * 4), series(self.item, "memory_percent", [85] * 4)]):
            findings, _ = self.run_engine(recent, history)
            self.assertNotIn("cpu_percent_oversized", findings)

    def test_historical_cpu_bursts_block_reduction_even_with_low_p95(self):
        findings, _ = self.run_engine([series(self.item, "cpu_percent", [7] * 4)],
                                      [series(self.item, "cpu_percent", [7] * 167 + [60], history=True)])
        self.assertNotIn("cpu_percent_oversized", findings)

    def test_short_or_sparse_history_never_means_overprovisioned(self):
        for stamps in ([NOW - index * 3600 for index in range(24)],
                       [NOW - index * 7200 for index in range(84)]):
            findings, summary = self.run_engine([series(self.item, "cpu_percent", [7] * 4)],
                                                [series(self.item, "cpu_percent", [7] * len(stamps), stamps=stamps)])
            self.assertNotIn("cpu_percent_oversized", findings)
            self.assertIn("insufficient_history", findings)
            self.assertEqual(summary["history_ready"], 0)

    def test_missing_or_stale_data_is_reported_and_not_counted_as_healthy(self):
        findings, summary = self.run_engine()
        self.assertIn("missing_telemetry", findings)
        self.assertEqual(summary["without_data"], 1)
        self.assertEqual(summary["cost_opportunities"], 0)

    def test_ebs_only_instance_without_local_disk_metrics_is_not_missing_telemetry(self):
        self.item = resource(provider="aws", resource_type="ec2_instance", status="running")
        reported = ("cpu_percent", "network_in_bytes_per_second", "network_out_bytes_per_second")
        findings, _ = self.run_engine([series(self.item, key, [7] * 4) for key in reported])
        self.assertNotIn("missing_telemetry", findings)
        findings, _ = self.run_engine([series(self.item, key, [7] * 4) for key in reported[:2]])
        self.assertEqual(findings["missing_telemetry"]["description"], "No recent readings for: Network out.")
        self.assertEqual(findings["missing_telemetry"]["evidence"]["explanation"],
                         "2 of 3 required metrics have recent readings.")

    def test_idle_event_resources_are_not_missing_telemetry_when_the_account_reports(self):
        topic = resource(provider="aws", resource_type="sns_topic", resource_id="idle-topic", status=None)
        vm = resource(id=2, provider="aws", resource_type="ec2_instance", resource_id="i-1", status="running")
        findings, _ = evaluate([topic], [], [], now=NOW)
        self.assertIn("missing_telemetry", rules(findings))
        findings, _ = evaluate([topic, vm], [series(vm, "cpu_percent", [7] * 4)], [], now=NOW)
        self.assertNotIn("missing_telemetry", {f["rule"] for f in findings if f["resource_id"] == topic["id"]})
        other_account = {**vm, "scope_id": "project-b"}
        findings, _ = evaluate([topic, other_account], [series(other_account, "cpu_percent", [7] * 4)], [], now=NOW)
        self.assertIn("missing_telemetry", {f["rule"] for f in findings if f["resource_id"] == topic["id"]})
        stale = series(vm, "cpu_percent", [7] * 4, stamps=[NOW - 7200 + i * 300 for i in range(4)])
        findings, _ = evaluate([topic, vm], [stale], [], now=NOW)
        self.assertIn("missing_telemetry", {f["rule"] for f in findings if f["resource_id"] == topic["id"]})

    def test_another_owners_same_cloud_resource_cannot_influence_results(self):
        outsider = resource(owner_id="owner-b")
        findings, summary = self.run_engine([series(outsider, "cpu_percent", [99] * 4)])
        self.assertNotIn("cpu_percent_high", findings)
        self.assertEqual(summary["monitored"], 0)

    def test_inactive_or_logical_resources_are_not_assessed(self):
        for item in (resource(status="STOPPED"), resource(resource_type="vpc_network")):
            findings, summary = evaluate([item], [], [], now=NOW)
            self.assertEqual(findings, [])
            self.assertEqual(summary["eligible"], 0)

    def test_no_vm_guest_metrics_are_invented(self):
        self.item = resource(provider="aws", resource_type="ec2_instance", status="running")
        findings, _ = self.run_engine([series(self.item, "cpu_percent", [7] * 4)])
        self.assertIn("capacity_visibility", findings)
        self.assertNotIn("memory_percent_high", findings)
        self.assertNotIn("filesystem_percent_oversized", findings)

    def test_storage_forecast_requires_sustained_convincing_growth(self):
        findings, _ = self.run_engine([series(self.item, "filesystem_percent", [80, 81, 82, 83])],
                                      [series(self.item, "filesystem_percent", list(range(60, 84)), history=True)])
        self.assertIn("storage_exhaustion", findings)
        self.assertAlmostEqual(findings["storage_exhaustion"]["evidence"]["value"], 17)
        declining, _ = self.run_engine([series(self.item, "filesystem_percent", [80] * 4)],
                                       [series(self.item, "filesystem_percent", list(range(99, 75, -1)), history=True)])
        self.assertNotIn("storage_exhaustion", declining)

    def test_network_change_is_not_mislabeled_as_saturation(self):
        self.item = resource(provider="aws", resource_type="ec2_instance", status="running")
        findings, _ = self.run_engine([series(self.item, "network_out_bytes_per_second", [4 * 1024 * 1024] * 4)],
                                      [series(self.item, "network_out_bytes_per_second", [1024 * 1024] * 168, history=True)])
        finding = findings["network_out_bytes_per_second_growth"]
        self.assertIn("bandwidth limit", finding["actions"][1])
        self.assertEqual(finding["kind"], "recommendation")

    def test_queue_and_delivery_alerts(self):
        self.item = resource(provider="aws", resource_type="sqs_queue", status=None)
        findings, _ = self.run_engine([series(self.item, "oldest_message_seconds", [950] * 4)])
        self.assertEqual(findings["oldest_message_seconds_high"]["severity"], "critical")
        self.item = resource(provider="aws", resource_type="sns_topic", status=None)
        findings, _ = self.run_engine([series(self.item, "failed_per_second", [0.2] * 4)])
        self.assertIn("failed_per_second_high", findings)

    def test_detached_ebs_review_requires_fresh_explicit_attachment_data(self):
        self.item = resource(provider="aws", resource_type="ebs_volume", status="available", raw_data={"Attachments": []})
        findings, _ = self.run_engine(telemetry_available=False)
        self.assertIn("unattached_volume", findings)
        for overrides in ({"raw_data": {}}, {"raw_data": {"Attachments": [{"InstanceId": "i-1"}]}},
                          {"last_seen_at": datetime.fromtimestamp(NOW - 3600, timezone.utc)}):
            item = {**self.item, **overrides}
            findings, _ = evaluate([item], [], [], now=NOW)
            self.assertNotIn("unattached_volume", rules(findings))

    def test_unattached_gcp_disk_is_reviewed_like_an_ebs_volume(self):
        disk = resource(provider="gcp", resource_type="persistent_disk", resource_id="364", status="READY",
                        attributes={"users": [], "size_gb": 50}, raw_data={})
        finding = rules(evaluate([disk], [], [], now=NOW, telemetry_available=False)[0])["unattached_volume"]
        self.assertIn("50 GB", finding["evidence"]["explanation"])
        for overrides in ({"attributes": {"users": ["vm-1"]}}, {"attributes": {}}, {"status": "CREATING"},
                          {"last_seen_at": datetime.fromtimestamp(NOW - 3600, timezone.utc)}):
            findings, _ = evaluate([{**disk, **overrides}], [], [], now=NOW)
            self.assertNotIn("unattached_volume", rules(findings))
        def checks(item):
            return confirmed_checks([item], [], [], now=NOW, telemetry_available=False, history_available=False)

        self.assertIn("1:unattached_volume", checks({**disk, "attributes": {"users": ["vm-1"]}}))
        self.assertNotIn("1:unattached_volume", checks({**disk, "attributes": {}}))

    def test_only_reserved_external_addresses_without_users_are_unused(self):
        address = resource(provider="gcp", resource_type="ip_address", resource_id="712", status="RESERVED",
                           attributes={"users": [], "address_type": "EXTERNAL", "address": "34.1.2.3"}, raw_data={})
        finding = rules(evaluate([address], [], [], now=NOW)[0])["unused_address"]
        self.assertIn("34.1.2.3", finding["evidence"]["explanation"])
        for overrides in ({"status": "IN_USE"}, {"attributes": {"users": ["lb"], "address_type": "EXTERNAL"}},
                          {"attributes": {"users": [], "address_type": "INTERNAL"}}):
            findings, _ = evaluate([{**address, **overrides}], [], [], now=NOW)
            self.assertNotIn("unused_address", rules(findings))

    def test_stopped_instance_reports_its_billed_volumes_once(self):
        mappings = {"BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-a"}}, {"Ebs": {"VolumeId": "vol-b"}}],
                    "StateTransitionReason": "User initiated (2026-10-01 10:00:00 GMT)"}
        vm = resource(provider="aws", resource_type="ec2_instance", resource_id="i-1", status="stopped", raw_data=mappings)
        volumes = [resource(id=index, provider="aws", resource_type="ebs_volume", resource_id=volume_id, status="in-use",
                            raw_data={"Size": size, "Attachments": [{"InstanceId": "i-1"}]})
                   for index, volume_id, size in ((2, "vol-a", 8), (3, "vol-b", 100))]
        findings, _ = evaluate([vm, *volumes], [], [], now=NOW)
        self.assertEqual([f["rule"] for f in findings], ["stopped_instance_storage"])
        finding = findings[0]
        self.assertEqual((finding["evidence"]["value"], finding["evidence"]["unit"]), (108, "GB"))
        self.assertIn("2 EBS volume(s)", finding["description"])
        self.assertIn("vol-a, vol-b", finding["evidence"]["explanation"])
        self.assertIn("User initiated", finding["evidence"]["explanation"])
        running, _ = evaluate([{**vm, "status": "running"}, *volumes], [], [], now=NOW)
        self.assertNotIn("stopped_instance_storage", rules(running))
        without_volumes, _ = evaluate([vm], [], [], now=NOW)
        self.assertNotIn("stopped_instance_storage", rules(without_volumes))
        checked = confirmed_checks([{**vm, "status": "running"}], [], [], now=NOW,
                                   telemetry_available=False, history_available=False)
        self.assertIn("1:stopped_instance_storage", checked)

    def test_findings_have_stable_ids_and_priority(self):
        recent = [series(self.item, "cpu_percent", [95] * 4), series(self.item, "filesystem_percent", [85] * 4)]
        first, _ = evaluate([self.item], recent, [], now=NOW)
        second, _ = evaluate([self.item], recent, [], now=NOW + 1)
        self.assertEqual([f["id"] for f in first], [f["id"] for f in second])
        self.assertEqual(first[0]["severity"], "critical")


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self):
        return json.dumps({"status": "success", "data": {"resultType": "matrix", "result": []}}).encode()


class RecommendationReportTests(unittest.TestCase):
    @patch("vanguard.recommendations.telemetry.urlopen", return_value=_Response())
    def test_history_is_owner_scoped_and_bounded(self, request):
        query_history([resource()], now=NOW)
        params = parse_qs(urlparse(request.call_args.args[0]).query)
        self.assertIn('owner_id="owner-a"', params["query"][0])
        self.assertIn("count_over_time", params["query"][0])
        self.assertIn('"__name__", "vanguard_cpu_percent"', params["query"][0])
        self.assertIn('"__name__", "vanguard_network_out_bytes_per_second"', params["query"][0])
        self.assertIn("or on (__name__, owner_id, provider, scope_id, region, resource_type, resource_id)", params["query"][0])
        self.assertEqual(params["step"], ["3600"])
        self.assertEqual((int(params["end"][0]) - int(params["start"][0])) / 3600 + 1, 168)

    def test_history_refuses_mixed_or_missing_owners(self):
        for items in ([resource(), resource(owner_id="owner-b")], [resource(owner_id=None)]):
            with self.assertRaises(ValueError):
                query_history(items)

    @patch("vanguard.recommendations.analysis.time.time", return_value=NOW)
    @patch("vanguard.recommendations.analysis.query_history", return_value=[])
    @patch("vanguard.recommendations.analysis.query_series")
    def test_analysis_returns_every_finding_for_persistence(self, recent, _history, _time):
        item = resource()
        recent.return_value = [series(item, "cpu_percent", [95] * 4)]
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [item]
        report = evaluate_inventory(connection, "owner-a")
        self.assertTrue(any(item["kind"] == "alert" for item in report["items"]))
        self.assertGreater(len(report["items"]), 1)
        self.assertGreater(report["summary"]["suggestions"], 0)
        self.assertEqual(connection.execute.call_args.args[1]["owner_id"], "owner-a")

    @patch("vanguard.recommendations.analysis.query_history")
    @patch("vanguard.recommendations.analysis.query_series", side_effect=MetricsUnavailable("offline"))
    def test_prometheus_outage_is_visible_and_does_not_fake_health(self, _recent, historical):
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [resource()]
        report = evaluate_inventory(connection, "owner-a")
        self.assertEqual(report["telemetry_status"], "unavailable")
        self.assertTrue(report["notices"])
        self.assertEqual(report["summary"]["without_data"], 1)
        historical.assert_not_called()

    @patch("vanguard.recommendations.analysis.time.time", return_value=NOW)
    @patch("vanguard.recommendations.analysis.query_history", side_effect=MetricsUnavailable("history offline"))
    @patch("vanguard.recommendations.analysis.query_series")
    def test_history_outage_preserves_live_alerts(self, recent, _history, _time):
        item = resource()
        recent.return_value = [series(item, "cpu_percent", [95] * 4)]
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [item]
        report = evaluate_inventory(connection, "owner-a")
        self.assertEqual(report["telemetry_status"], "partial")
        self.assertEqual(report["summary"]["alerts"], 1)
        self.assertFalse(any(f["rule"] == "insufficient_history" for f in report["items"]))

    def test_routes_require_authentication_and_validate_filters(self):
        from fastapi import HTTPException
        def database():
            return MagicMock()
        def unauthorized():
            raise HTTPException(401, "Authentication required")
        app = FastAPI()
        database_type = Annotated[object, Depends(database)]
        user_type = Annotated[dict, Depends(unauthorized)]
        app.include_router(create_router(database_type, database_type, user_type, user_type))
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/recommendations").status_code, 401)
            self.assertEqual(client.get("/api/recommendations/summary").status_code, 401)
        app.dependency_overrides[unauthorized] = lambda: {"id": "owner-a"}
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/recommendations?severity=bogus").status_code, 422)
            self.assertEqual(client.get("/api/recommendations?limit=101").status_code, 422)


if __name__ == "__main__":
    unittest.main()
