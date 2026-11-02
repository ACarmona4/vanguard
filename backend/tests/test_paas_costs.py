"""Billing contracts, graceful fallback and scoped PaaS economic evidence."""

import unittest
from datetime import date, timedelta
from unittest.mock import patch

from botocore.exceptions import ClientError
from test_recommendation_economics import (
    TODAY,
    _Connection,
    finding,
    inventory,
    line,
    source,
)
from vanguard.costs.catalog import category_for
from vanguard.costs.collectors import collect_aws
from vanguard.costs.identity import billing_key, resource_billing_key
from vanguard.recommendations.economics import assess


def costs(service, identifier=None):
    return {"ResultsByTime": [{"TimePeriod": {"Start": (date.today()-timedelta(days=1)).isoformat()},
                              "Groups": [{"Keys": [service, identifier] if identifier else [service],
                                          "Metrics": {"UnblendedCost": {"Amount": "2.50", "Unit": "USD"}}}]}]}


class PaaSCostTests(unittest.TestCase):
    def test_identifiers_cover_paas_and_reject_objects_jobs_and_ambiguous_names(self):
        cases = {
            ("aws", "arn:aws:lambda:us-east-1:123:function:api:12"): ("lambda_function", "arn:aws:lambda:us-east-1:123:function:api"),
            ("aws", "arn:aws:rds:us-east-1:123:db:orders"): ("rds_instance", "arn:aws:rds:us-east-1:123:db:orders"),
            ("aws", "arn:aws:rds:us-east-1:123:cluster:orders"): ("rds_cluster", "arn:aws:rds:us-east-1:123:cluster:orders"),
            ("aws", "arn:aws:rds:us-east-1:123:snapshot:manual"): ("rds_snapshot", "arn:aws:rds:us-east-1:123:snapshot:manual"),
            ("aws", "arn:aws:dynamodb:us-east-1:123:table/orders"): ("dynamodb_table", "arn:aws:dynamodb:us-east-1:123:table/orders"),
            ("aws", "arn:aws:sns:us-east-1:123:events"): ("sns_topic", "arn:aws:sns:us-east-1:123:events"),
            ("aws", "arn:aws:s3:::archive"): ("s3_bucket", "archive"),
            ("gcp", "//sqladmin.googleapis.com/projects/p/instances/db"): ("cloudsql_instance", "db"),
            ("gcp", "//bigquery.googleapis.com/projects/p/datasets/d/tables/t"): ("bigquery_table", "d.t"),
            ("gcp", "//pubsub.googleapis.com/projects/p/topics/events"): ("pubsub_topic", "projects/p/topics/events"),
            ("gcp", "//pubsub.googleapis.com/projects/p/subscriptions/events"): ("pubsub_subscription", "projects/p/subscriptions/events"),
        }
        for (provider, billed), expected in cases.items():
            with self.subTest(billed=billed):
                self.assertEqual(billing_key(provider, billed), expected)
        for billed in ("orders", "arn:aws:s3:::archive/object", "//bigquery.googleapis.com/projects/p/jobs/abc"):
            self.assertIsNone(billing_key("aws" if billed.startswith("arn:") else "gcp", billed))
        queue = inventory(rtype="sqs_queue", provider="aws", cloud_id="https://sqs.us-east-1.amazonaws.com/123/events")
        self.assertEqual(resource_billing_key(queue), billing_key("aws", "arn:aws:sqs:us-east-1:123:events"))

    @patch("vanguard.costs.collectors.boto3.Session")
    def test_aws_resource_detail_requests_service_and_resource_not_just_ec2(self, factory):
        client = factory.return_value.client.return_value
        client.get_cost_and_usage.return_value = costs("AWS Lambda")
        client.get_cost_and_usage_with_resources.return_value = costs("AWS Lambda", "arn:aws:lambda:us-east-1:123:function:api")
        records, available = collect_aws({"owner_id": "o", "connection_id": "c", "provider": "aws", "scope_id": "123"}, {"access_key_id": "test", "secret_access_key": "test"}, date.today()-timedelta(days=30), date.today())
        self.assertTrue(available)
        self.assertEqual([r["level"] for r in records], ["service", "resource"])
        self.assertEqual(records[1]["service"], "AWS Lambda")
        request = client.get_cost_and_usage_with_resources.call_args.kwargs
        self.assertEqual([g["Key"] for g in request["GroupBy"]], ["SERVICE", "RESOURCE_ID"])
        self.assertNotIn("Amazon Elastic Compute Cloud - Compute", str(request["Filter"]))
        self.assertGreaterEqual(date.fromisoformat(request["TimePeriod"]["Start"]), date.today()-timedelta(days=14))

    @patch("vanguard.costs.collectors.boto3.Session")
    def test_legacy_api_falls_back_without_losing_service_totals(self, factory):
        client = factory.return_value.client.return_value
        client.get_cost_and_usage.return_value = costs("AWS Lambda")
        legacy = costs("i-abc")
        denied = ClientError({"Error": {"Code": "ValidationException", "Message": "legacy filter required"}}, "GetCostAndUsageWithResources")
        client.get_cost_and_usage_with_resources.side_effect = [denied, legacy]
        records, available = collect_aws({"owner_id": "o", "connection_id": "c", "provider": "aws", "scope_id": "123"}, {"access_key_id": "test", "secret_access_key": "test"}, date.today()-timedelta(days=7), date.today())
        self.assertTrue(available)
        self.assertEqual(records[0]["service"], "AWS Lambda")
        self.assertEqual(records[1]["resource_id"], "i-abc")
        self.assertEqual(client.get_cost_and_usage_with_resources.call_count, 2)

    @patch("vanguard.costs.collectors.boto3.Session")
    def test_resource_access_denied_keeps_paas_totals_and_does_not_fabricate_detail(self, factory):
        client = factory.return_value.client.return_value
        client.get_cost_and_usage.return_value = costs("AWS Lambda")
        client.get_cost_and_usage_with_resources.side_effect = ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetCostAndUsageWithResources")
        records, available = collect_aws({"owner_id": "o", "connection_id": "c", "provider": "aws", "scope_id": "123"}, {"access_key_id": "test", "secret_access_key": "test"}, date.today()-timedelta(days=7), date.today())
        self.assertFalse(available)
        self.assertEqual([r["level"] for r in records], ["service"])

    def test_paas_cost_review_has_current_cost_without_invented_savings(self):
        arn = "arn:aws:lambda:us-east-1:123:function:api"
        function = inventory(rtype="lambda_function", cloud_id=arn, provider="aws")
        records = [line(arn+":1", day, 2, provider="aws") for day in range(7)]
        [result] = assess(_Connection(records, [source(provider="aws")]), "o1", [finding("serverless_idle_capacity")], [function], today=TODAY)
        self.assertEqual(result["cost"]["monthly_cost"], 60)
        self.assertIsNone(result["cost"]["savings"])
        foreign = [dict(record, scope_id="other-account") for record in records]
        [result] = assess(_Connection(foreign, [source(provider="aws")]), "o1", [finding("serverless_idle_capacity")], [{**function, "scope_id": "123"}], today=TODAY)
        self.assertEqual(result["cost"]["status"], "not_attributable")

    def test_bigquery_table_does_not_inherit_entire_dataset_bill(self):
        records = [line("//bigquery.googleapis.com/projects/p/datasets/d", 1, 10)]
        [result] = assess(_Connection(records, [source()]), "o1", [finding("bigquery_partitioning")], [inventory(rtype="bigquery_table", cloud_id="d.t")], today=TODAY)
        self.assertEqual(result["cost"]["status"], "not_attributable")
        self.assertEqual(category_for("BigQuery"), "Analytics")
        self.assertEqual(category_for("AWS Lambda"), "Serverless")
        self.assertEqual(category_for("Amazon Simple Notification Service"), "Messaging")

    def test_gen2_function_receives_costs_of_its_known_backing_service(self):
        function = inventory(rtype="cloud_function", cloud_id="us-central1/processor", region="us-central1",
                             attributes={"generation": "GEN_2", "run_service": "processor-run"})
        records = [line("//run.googleapis.com/projects/p/locations/us-central1/services/processor-run", 1, 2)]
        [result] = assess(_Connection(records, [source()]), "o1", [finding("serverless_idle_capacity")], [function], today=TODAY)
        self.assertEqual(result["cost"]["observed_amount"], 2)
        foreign = {**function, "region": "europe-west1"}
        [result] = assess(_Connection(records, [source()]), "o1", [finding("serverless_idle_capacity")], [foreign], today=TODAY)
        self.assertEqual(result["cost"]["status"], "not_attributable")
