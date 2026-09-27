import unittest

from vanguard_inventory.utilization.catalog import metrics_for


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


if __name__ == "__main__":
    unittest.main()
