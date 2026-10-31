"""AWS managed-service inventory: detail calls enrich resources but never drop them."""

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from vanguard.inventory.collectors.aws import AWSCollector


class _Denied(Exception):
    pass


def collector(**clients):
    item = object.__new__(AWSCollector)
    item.account_id = "123456789012"
    item.regions = ["us-east-1"]
    item._client = lambda service, **_: clients[service]
    return item


def paginated(key, values):
    return Mock(paginate=Mock(return_value=[{key: values}]))


class AWSManagedServiceTests(unittest.TestCase):
    def test_sqs_queue_keeps_dead_letter_polling_and_tags(self):
        sqs = Mock()
        sqs.get_paginator.return_value = paginated("QueueUrls", [
            "https://sqs.us-east-1.amazonaws.com/123/orders", "https://sqs.us-east-1.amazonaws.com/123/locked"])
        sqs.get_queue_attributes.side_effect = [
            {"Attributes": {"RedrivePolicy": json.dumps({"deadLetterTargetArn": "arn:aws:sqs:us-east-1:123:orders-dlq"}),
                            "ReceiveMessageWaitTimeSeconds": "20", "MessageRetentionPeriod": "345600",
                            "VisibilityTimeout": "30", "FifoQueue": "false"}},
            _Denied("sqs:GetQueueAttributes"),
        ]
        sqs.list_queue_tags.return_value = {"Tags": {"team": "payments"}}

        orders, locked = collector(sqs=sqs).sqs_queues("us-east-1")

        self.assertEqual(orders.attributes["dead_letter_queue"], "arn:aws:sqs:us-east-1:123:orders-dlq")
        self.assertEqual((orders.attributes["long_polling_seconds"], orders.attributes["retention_seconds"]), (20, 345600))
        self.assertEqual(orders.attributes["tags"], {"team": "payments"})
        self.assertEqual(locked.name, "locked")
        self.assertIsNone(locked.attributes["dead_letter_queue"])
        self.assertIsNone(locked.attributes["long_polling_seconds"])

    def test_sns_topic_counts_confirmed_subscriptions(self):
        sns = Mock()
        sns.get_paginator.return_value = paginated("Topics", [{"TopicArn": "arn:aws:sns:us-east-1:123:alerts"}])
        sns.get_topic_attributes.return_value = {"Attributes": {"SubscriptionsConfirmed": "0", "SubscriptionsPending": "1"}}
        sns.list_tags_for_resource.side_effect = _Denied("sns:ListTagsForResource")

        [topic] = collector(sns=sns).sns_topics("us-east-1")

        self.assertEqual((topic.attributes["subscriptions"], topic.attributes["subscriptions_pending"]), (0, 1))
        self.assertEqual(topic.attributes["tags"], {})

    def test_dynamodb_table_normalizes_billing_mode_and_capacity(self):
        dynamodb = Mock()
        dynamodb.get_paginator.return_value = paginated("TableNames", ["sessions", "events"])
        dynamodb.describe_table.side_effect = [
            {"Table": {"TableArn": "arn:sessions", "TableStatus": "ACTIVE", "TableSizeBytes": 2048,
                       "ProvisionedThroughput": {"ReadCapacityUnits": 50, "WriteCapacityUnits": 10}}},
            {"Table": {"TableArn": "arn:events", "TableStatus": "ACTIVE",
                       "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"},
                       "ProvisionedThroughput": {"ReadCapacityUnits": 0, "WriteCapacityUnits": 0}}},
        ]
        dynamodb.list_tags_of_resource.return_value = {"Tags": [{"Key": "env", "Value": "dev"}]}

        provisioned, on_demand = collector(dynamodb=dynamodb).dynamodb_tables("us-east-1")

        self.assertEqual(provisioned.attributes["billing_mode"], "PROVISIONED")
        self.assertEqual((provisioned.attributes["provisioned_read"], provisioned.attributes["provisioned_write"]), (50, 10))
        self.assertEqual(provisioned.attributes["tags"], {"env": "dev"})
        self.assertEqual(on_demand.attributes["billing_mode"], "PAY_PER_REQUEST")

    def test_s3_buckets_keep_lifecycle_and_degrade_without_detail_permissions(self):
        s3 = Mock()
        s3.list_buckets.return_value = {"Buckets": [
            {"Name": "logs-archive", "CreationDate": datetime(2026, 1, 1, tzinfo=timezone.utc)},
            {"Name": "legacy-assets"},
        ]}
        s3.get_bucket_location.side_effect = [{"LocationConstraint": "eu-west-1"}, {"LocationConstraint": None}]
        s3.get_bucket_lifecycle_configuration.side_effect = [
            {"Rules": [{"ID": "expire", "Expiration": {"Days": 90}},
                       {"ID": "uploads", "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}}]},
            _Denied("NoSuchLifecycleConfiguration"),
        ]
        s3.get_bucket_versioning.return_value = {"Status": "Enabled"}
        s3.get_bucket_tagging.side_effect = _Denied("NoSuchTagSet")
        s3.list_bucket_intelligent_tiering_configurations.return_value = {}

        archive, legacy = collector(s3=s3).s3_buckets()

        self.assertEqual((archive.resource_type, archive.resource_id, archive.region), ("s3_bucket", "logs-archive", "eu-west-1"))
        self.assertEqual(archive.attributes["lifecycle_rules"], 2)
        self.assertTrue(archive.attributes["aborts_incomplete_uploads"])
        self.assertTrue(archive.attributes["versioning"])
        self.assertEqual(archive.attributes["created_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(legacy.region, "us-east-1")
        self.assertEqual(legacy.attributes["lifecycle_rules"], 0)
        self.assertFalse(legacy.attributes["aborts_incomplete_uploads"])
        self.assertEqual(legacy.attributes["tags"], {})


if __name__ == "__main__":
    unittest.main()
