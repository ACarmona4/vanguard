import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from vanguard.inventory.taxonomy import resource_types_for
from vanguard.utilization.catalog import metrics_for
from vanguard.utilization.service import query_series


class _PrometheusResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"status":"success","data":{"resultType":"matrix","result":[]}}'


class NativeMetricsCatalogTests(unittest.TestCase):
    def test_virtual_machines_only_expose_provider_native_metrics(self):
        for provider, resource_type in (
            ("aws", "ec2_instance"),
            ("gcp", "compute_instance"),
        ):
            metrics = metrics_for({"provider": provider, "resource_type": resource_type})
            keys = {metric.key for metric in metrics}
            self.assertIn("cpu_percent", keys)
            self.assertNotIn("memory_percent", keys)
            self.assertNotIn("filesystem_percent", keys)

    def test_managed_service_keeps_native_memory_and_disk_metrics(self):
        metrics = metrics_for({
            "provider": "gcp",
            "resource_type": "cloudsql_instance",
            "attributes": {"database_version": "POSTGRES_15"},
        })
        keys = {metric.key for metric in metrics}
        self.assertIn("memory_percent", keys)
        self.assertIn("filesystem_percent", keys)

    def test_managed_service_traffic_metrics_never_require_traffic(self):
        cases = {
            ("aws", "sqs_queue"): {"messages_sent_per_second", "messages_received_per_second", "empty_receives_per_second"},
            ("gcp", "pubsub_topic"): {"published_per_second"},
            ("gcp", "pubsub_subscription"): {"acked_per_second"},
        }
        for (provider, resource_type), keys in cases.items():
            metrics = {metric.key: metric for metric in metrics_for({"provider": provider, "resource_type": resource_type})}
            for key in keys:
                with self.subTest(resource_type=resource_type, key=key):
                    self.assertTrue(metrics[key].activity)
        dynamodb = {metric.key: metric for metric in metrics_for({"provider": "aws", "resource_type": "dynamodb_table"})}
        self.assertTrue(dynamodb["provisioned_read_units"].optional)
        self.assertTrue(dynamodb["provisioned_write_units"].optional)

    def test_storage_groups_include_object_buckets(self):
        self.assertIn("storage_bucket", resource_types_for("storage"))
        self.assertIn("s3_bucket", resource_types_for("storage"))
        self.assertEqual(metrics_for({"provider": "gcp", "resource_type": "storage_bucket"}), ())

    def test_shared_filters_group_equivalent_cloud_resources(self):
        self.assertEqual(
            set(resource_types_for("instances")),
            {"ec2_instance", "compute_instance"},
        )
        self.assertEqual(
            set(resource_types_for("databases")),
            {"dynamodb_table", "cloudsql_instance"},
        )
        self.assertIsNone(resource_types_for("ec2_instance"))

    @patch("vanguard.utilization.service.urlopen", return_value=_PrometheusResponse())
    def test_prometheus_query_is_scoped_to_the_authenticated_owner(self, mocked_open):
        resources = [
            {"owner_id": "owner-a", "provider": "aws", "scope_id": "123", "region": region,
             "resource_type": "ec2_instance", "resource_id": resource_id}
            for region, resource_id in (("us-east-1", "i-1"), ("us-west-2", "i-2"))
        ]

        query_series(resources)

        query = parse_qs(urlparse(mocked_open.call_args.args[0]).query)["query"][0]
        self.assertIn('owner_id="owner-a"', query)


if __name__ == "__main__":
    unittest.main()
