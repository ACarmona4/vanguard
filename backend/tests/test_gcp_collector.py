import unittest
from unittest.mock import Mock

from vanguard_inventory.collectors.gcp import GCPCollector


class _Response:
    def __init__(self, reason):
        self.reason = reason

    def json(self):
        return {"error": {"details": [{"reason": self.reason}]}}


class _CollectorError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.response = _Response(reason)


def _collector_with_sql_error(reason):
    collector = GCPCollector("test-project")
    collector.sql_instances = Mock(side_effect=_CollectorError(reason))
    for method in (
        "instances",
        "networks",
        "subnetworks",
        "firewalls",
        "pubsub_topics",
        "pubsub_subscriptions",
        "service_accounts",
        "project_policy",
    ):
        setattr(collector, method, Mock(return_value=[]))
    return collector


class GCPCollectorTests(unittest.TestCase):
    def test_disabled_optional_api_is_treated_as_an_empty_resource_type(self):
        result = _collector_with_sql_error("SERVICE_DISABLED").collect()

        self.assertEqual(result.resources, [])
        self.assertEqual(result.errors, [])

    def test_access_not_configured_is_treated_as_an_empty_resource_type(self):
        result = _collector_with_sql_error("accessNotConfigured").collect()

        self.assertEqual(result.errors, [])

    def test_real_permission_error_remains_visible(self):
        result = _collector_with_sql_error("IAM_PERMISSION_DENIED").collect()

        self.assertEqual(len(result.errors), 1)
        self.assertIn("cloudsql.instances", result.errors[0])


if __name__ == "__main__":
    unittest.main()
