"""Observed-cost attribution: only billed resource lines, never invented prices."""

import unittest
from datetime import date, timedelta
from decimal import Decimal

from vanguard.recommendations.economics import assess, billing_key, suggested_impact

TODAY = date(2026, 10, 26)
GCP_DISK = "//compute.googleapis.com/projects/516505046761/zones/us-central1-a/disk/364"


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, records, sources):
        self.records, self.sources = records, sources

    def execute(self, query, params):
        if "FROM cost_records" in query:
            owner, start, end = params
            return _Rows([r for r in self.records if r["owner"] == owner and start <= r["usage_date"] < end])
        return _Rows(self.sources)


def line(resource_id, days_ago, amount, *, connection="c1", provider="gcp", currency="USD", owner="o1"):
    return {"owner": owner, "connection_id": connection, "provider": provider, "resource_id": resource_id,
            "usage_date": TODAY - timedelta(days=days_ago), "currency": currency, "amount": Decimal(str(amount))}


def source(connection="c1", provider="gcp", status="success", detail=True):
    return {"connection_id": connection, "provider": provider, "status": status, "detail_available": detail}


def inventory(rid=1, rtype="persistent_disk", cloud_id="364", provider="gcp", connection="c1", **extra):
    return {"id": rid, "connection_id": connection, "provider": provider, "resource_type": rtype,
            "resource_id": cloud_id, **extra}


def finding(rule="unattached_volume", rid=1):
    return {"rule": rule, "resource_id": rid}


class BillingKeyTests(unittest.TestCase):
    def test_provider_billing_names_map_to_inventory_identities(self):
        cases = {
            ("gcp", GCP_DISK): ("persistent_disk", "364"),
            ("gcp", "//compute.googleapis.com/projects/1/zones/us-central1-a/instances/648"): ("compute_instance", "648"),
            ("gcp", "//compute.googleapis.com/projects/1/regions/us-central1/addresses/712"): ("ip_address", "712"),
            ("gcp", "//compute.googleapis.com/projects/1/global/addresses/9"): ("ip_address", "9"),
            ("gcp", "//storage.googleapis.com/projects/_/buckets/acme.web-assets_2"): ("storage_bucket", "acme.web-assets_2"),
            ("gcp", "//run.googleapis.com/projects/516/locations/us-central1/services/api"): ("cloud_run_service", "us-central1/api"),
            ("gcp", "//cloudfunctions.googleapis.com/projects/516/locations/us-east1/functions/thumb"): ("cloud_function", "us-east1/thumb"),
            ("gcp", "//bigquery.googleapis.com/projects/p/datasets/billing_export"): ("bigquery_dataset", "billing_export"),
            ("aws", "i-0abc"): ("ec2_instance", "i-0abc"),
            ("aws", "vol-0abc"): ("ebs_volume", "vol-0abc"),
        }
        for (provider, billed), expected in cases.items():
            with self.subTest(billed=billed):
                self.assertEqual(billing_key(provider, billed), expected)

    def test_unknown_or_ambiguous_billing_names_are_not_matched(self):
        for provider, billed in (("gcp", "//storage.googleapis.com/projects/_/buckets/Invalid Name"),
                                 ("gcp", "//bigquery.googleapis.com/projects/p/jobs/job_abc"),
                                 ("gcp", "//compute.googleapis.com/projects/1/zones/z/instances/name-not-id"),
                                 ("aws", "arn:aws:s3:::bucket"), ("gcp", None), ("aws", "")):
            with self.subTest(billed=billed):
                self.assertIsNone(billing_key(provider, billed))


class AssessTests(unittest.TestCase):
    def run_assess(self, records, items, resources, sources=(source(),)):
        return assess(_Connection(records, list(sources)), "o1", items, resources, today=TODAY)

    def test_removable_resource_gets_savings_with_formula_and_confidence(self):
        records = [line(GCP_DISK, day, 2) for day in range(14)]
        [result] = self.run_assess(records, [finding()], [inventory()])
        cost = result["cost"]
        self.assertEqual((cost["status"], cost["currency"], cost["days_covered"]), ("attributed", "USD", 14))
        self.assertEqual((cost["observed_amount"], cost["daily_average"], cost["monthly_cost"]), (28.0, 2.0, 60.0))
        self.assertEqual(cost["source"], "gcp_billing_export")
        self.assertEqual(cost["savings"]["monthly_amount"], 60.0)
        self.assertEqual(cost["savings"]["confidence"], "high")
        self.assertIn("× 30 days", cost["savings"]["formula"])
        self.assertTrue(any("before credits" in item for item in cost["savings"]["assumptions"]))
        self.assertEqual(suggested_impact(cost), "high")

    def test_confidence_follows_the_number_of_billed_days(self):
        for days, confidence in ((14, "high"), (7, "medium"), (3, "low")):
            with self.subTest(days=days):
                [result] = self.run_assess([line(GCP_DISK, day, 1) for day in range(days)], [finding()], [inventory()])
                self.assertEqual(result["cost"]["savings"]["confidence"], confidence)

    def test_billing_lines_only_match_the_same_connection_and_owner(self):
        records = [line(GCP_DISK, 1, 5, connection="other"), line(GCP_DISK, 1, 5, owner="o2")]
        [result] = self.run_assess(records, [finding()], [inventory()])
        self.assertEqual(result["cost"]["status"], "not_attributable")
        self.assertIsNone(result["cost"]["savings"])

    def test_old_lines_outside_the_lookback_window_are_ignored(self):
        [result] = self.run_assess([line(GCP_DISK, 45, 5)], [finding()], [inventory()])
        self.assertEqual(result["cost"]["status"], "not_attributable")

    def test_missing_resource_detail_explains_why_cost_is_not_attributable(self):
        aws = inventory(rtype="ebs_volume", cloud_id="vol-1", provider="aws")
        [result] = self.run_assess([], [finding()], [aws], [source(provider="aws", detail=False)])
        self.assertIn("resource-level data is not available", result["cost"]["reason"])
        [result] = self.run_assess([], [finding()], [inventory()], [])
        self.assertIn("No billing source", result["cost"]["reason"])
        [result] = self.run_assess([], [finding()], [inventory()])
        self.assertIn("No billing lines", result["cost"]["reason"])

    def test_mixed_currencies_are_never_added_together(self):
        records = [line(GCP_DISK, 1, 5), line(GCP_DISK, 2, 9000, currency="COP")]
        [result] = self.run_assess(records, [finding()], [inventory()])
        self.assertEqual(result["cost"]["status"], "not_attributable")
        self.assertIn("more than one currency", result["cost"]["reason"])

    def test_rightsizing_shows_current_cost_without_inventing_savings(self):
        vm = inventory(rtype="compute_instance", cloud_id="648")
        billed = "//compute.googleapis.com/projects/1/zones/us-central1-a/instances/648"
        [result] = self.run_assess([line(billed, 1, 3)], [finding("cpu_percent_oversized")], [vm])
        self.assertEqual(result["cost"]["monthly_cost"], 90.0)
        self.assertIsNone(result["cost"]["savings"])
        self.assertIn("price of a smaller size", result["cost"]["reason"])
        self.assertIsNone(suggested_impact(result["cost"]))

    def test_technical_alerts_get_no_cost_block(self):
        [result] = self.run_assess([line(GCP_DISK, 1, 5)], [finding("cpu_percent_high")], [inventory()])
        self.assertNotIn("cost", result)

    def test_stopped_instance_savings_add_up_its_attached_volumes(self):
        vm = inventory(rtype="ec2_instance", cloud_id="i-1", provider="aws",
                       raw_data={"BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-a"}}, {"Ebs": {"VolumeId": "vol-b"}}]})
        volumes = [inventory(2, "ebs_volume", "vol-a", "aws"), inventory(3, "ebs_volume", "vol-b", "aws")]
        records = [line("vol-a", 1, 1, provider="aws"), line("vol-b", 1, 2, provider="aws")]
        [result] = self.run_assess(records, [finding("stopped_instance_storage")], [vm, *volumes])
        self.assertEqual(result["cost"]["savings"]["monthly_amount"], 90.0)
        self.assertIn("attached volumes", result["cost"]["savings"]["formula"])
        [partial] = self.run_assess(records[:1], [finding("stopped_instance_storage")], [vm, *volumes])
        self.assertEqual(partial["cost"]["status"], "not_attributable")

    def test_zero_billed_cost_yields_no_savings(self):
        [result] = self.run_assess([line(GCP_DISK, 1, 0)], [finding()], [inventory()])
        self.assertIsNone(result["cost"]["savings"])
        self.assertIn("no billed cost", result["cost"]["reason"])

    def test_suggested_impact_thresholds(self):
        for amount, impact in ((50, "high"), (10, "medium"), (0.5, "low")):
            with self.subTest(amount=amount):
                self.assertEqual(suggested_impact({"savings": {"monthly_amount": amount}}), impact)
        self.assertIsNone(suggested_impact(None))


if __name__ == "__main__":
    unittest.main()
