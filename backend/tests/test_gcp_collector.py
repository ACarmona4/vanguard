import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from vanguard.inventory.collectors.gcp import GCPCollector


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
    collector = GCPCollector("test-project", credentials=object())
    collector.sql_instances = Mock(side_effect=_CollectorError(reason))
    for method in (
        "instances",
        "disks",
        "addresses",
        "storage_buckets",
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


    def test_disks_and_addresses_keep_the_fields_used_by_recommendations(self):
        from google.cloud import compute_v1

        disk = compute_v1.Disk(
            id=3640886699450654514, name="old-data", size_gb=50, status="READY",
            zone="https://www.googleapis.com/compute/v1/projects/p/zones/us-central1-a",
            type_="https://www.googleapis.com/compute/v1/projects/p/zones/us-central1-a/diskTypes/pd-standard",
            users=[], creation_timestamp="2026-01-01T00:00:00Z",
        )
        regional = compute_v1.Address(
            id=7120527120856735507, name="spare-ip", address="34.1.2.3", status="RESERVED",
            address_type="EXTERNAL", region="https://www.googleapis.com/compute/v1/projects/p/regions/us-central1",
        )
        used = compute_v1.Address(id=149024678021526098, name="lb-ip", status="IN_USE", address_type="EXTERNAL",
                                  users=["https://www.googleapis.com/compute/v1/projects/p/global/forwardingRules/web"])
        disks = Mock(aggregated_list=Mock(return_value=[("zones/us-central1-a", SimpleNamespace(disks=[disk]))]))
        addresses = Mock(aggregated_list=Mock(return_value=[("regions/us-central1", SimpleNamespace(addresses=[regional]))]))
        global_addresses = Mock(list=Mock(return_value=[used]))
        collector = GCPCollector("p", credentials=object())
        with patch.object(compute_v1, "DisksClient", return_value=disks), \
                patch.object(compute_v1, "AddressesClient", return_value=addresses), \
                patch.object(compute_v1, "GlobalAddressesClient", return_value=global_addresses):
            [found_disk] = collector.disks()
            spare, in_use = collector.addresses()

        self.assertEqual((found_disk.resource_type, found_disk.resource_id), ("persistent_disk", "3640886699450654514"))
        self.assertEqual((found_disk.region, found_disk.zone, found_disk.status), ("us-central1", "us-central1-a", "READY"))
        self.assertEqual(found_disk.attributes["users"], [])
        self.assertEqual((found_disk.attributes["size_gb"], found_disk.attributes["disk_type"]), (50, "pd-standard"))
        self.assertEqual((spare.resource_type, spare.resource_id, spare.region), ("ip_address", "7120527120856735507", "us-central1"))
        self.assertEqual((spare.status, spare.attributes["address_type"], spare.attributes["users"]), ("RESERVED", "EXTERNAL", []))
        self.assertEqual((in_use.region, in_use.attributes["users"]), ("global", ["web"]))

    def test_storage_buckets_keep_class_location_and_lifecycle(self):
        page = {"items": [
            {"name": "acme-logs", "location": "US", "locationType": "multi-region", "storageClass": "STANDARD",
             "lifecycle": {"rule": [{"action": {"type": "Delete"}, "condition": {"age": 30}},
                                    {"action": {"type": "SetStorageClass", "storageClass": "COLDLINE"}}]},
             "softDeletePolicy": {"retentionDurationSeconds": "604800"}, "versioning": {"enabled": True},
             "labels": {"team": "data"}, "timeCreated": "2026-01-01T00:00:00Z"},
            {"name": "acme.web-assets", "location": "US-CENTRAL1", "locationType": "region",
             "storageClass": "STANDARD", "autoclass": {"enabled": True}},
        ]}
        response = Mock(json=Mock(return_value=page))
        session = Mock(get=Mock(return_value=response))
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        with patch("google.auth.transport.requests.AuthorizedSession", return_value=session):
            logs, assets = GCPCollector("p", credentials=object()).storage_buckets()

        self.assertEqual(session.get.call_args.kwargs["params"], {"project": "p"})
        self.assertEqual((logs.resource_type, logs.resource_id, logs.region), ("storage_bucket", "acme-logs", "us"))
        self.assertEqual((logs.attributes["location_type"], logs.attributes["storage_class"]), ("multi-region", "STANDARD"))
        self.assertEqual(logs.attributes["lifecycle_rules"], 2)
        self.assertEqual(logs.attributes["lifecycle_actions"], ["Delete", "SetStorageClass"])
        self.assertEqual(logs.attributes["soft_delete_seconds"], 604800)
        self.assertTrue(logs.attributes["versioning"])
        self.assertFalse(logs.attributes["autoclass"])
        self.assertEqual((assets.resource_id, assets.region), ("acme.web-assets", "us-central1"))
        self.assertTrue(assets.attributes["autoclass"])
        self.assertEqual(assets.attributes["lifecycle_rules"], 0)

    def test_pubsub_topics_count_subscriptions_and_survive_a_denied_listing(self):
        from datetime import timedelta

        from google.cloud import pubsub_v1

        topics = [pubsub_v1.types.Topic(name="projects/p/topics/orders", message_retention_duration=timedelta(days=1)),
                  pubsub_v1.types.Topic(name="projects/p/topics/locked")]
        client = Mock(list_topics=Mock(return_value=topics))
        client.list_topic_subscriptions.side_effect = [["projects/p/subscriptions/a", "projects/p/subscriptions/b"],
                                                       PermissionError("pubsub.topics.listSubscriptions")]
        with patch.object(pubsub_v1, "PublisherClient", return_value=client):
            orders, locked = GCPCollector("p", credentials=object()).pubsub_topics()

        self.assertEqual((orders.attributes["subscriptions"], orders.attributes["retention_seconds"]), (2, 86400))
        self.assertIsNone(locked.attributes["subscriptions"])
        self.assertIsNone(locked.attributes["retention_seconds"])

if __name__ == "__main__":
    unittest.main()
