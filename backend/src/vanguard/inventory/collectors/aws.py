from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from botocore.config import Config

from ..models import CollectionResult, Resource


def _tags(tags: list[dict[str, str]] | None) -> dict[str, str]:
    return {tag["Key"]: tag["Value"] for tag in tags or []}


def _optional(read: Callable[[], Any], default: Any = None) -> Any:
    """Detail calls may lack a permission; the resource is still inventoried without them."""
    try:
        return read()
    except Exception:
        return default


def _number(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class AWSCollector:
    def __init__(
        self,
        credentials: dict[str, str],
        regions: Iterable[str] | None = None,
    ):
        import boto3

        if not credentials:
            raise ValueError("Saved AWS credentials are required")
        self.session = boto3.Session(
            aws_access_key_id=credentials["access_key_id"],
            aws_secret_access_key=credentials["secret_access_key"],
            aws_session_token=credentials.get("session_token") or None,
        )
        self.account_id = self._client("sts").get_caller_identity()["Account"]
        requested = [region.strip() for region in regions or [] if region.strip()]
        self.regions = requested or ([self.session.region_name] if self.session.region_name else [])
        if not self.regions:
            ec2 = self._client("ec2", region_name="us-east-1")
            self.regions = sorted(item["RegionName"] for item in ec2.describe_regions()["Regions"])

    def _client(self, service, **kwargs):
        return self.session.client(service, config=Config(
            connect_timeout=5, read_timeout=20, retries={"max_attempts": 2}
        ), **kwargs)

    def collect(self) -> CollectionResult:
        result = CollectionResult()
        for region in self.regions:
            regional: list[tuple[str, Callable[[], list[Resource]]]] = [
                ("ec2.instances", lambda region=region: self.ec2_instances(region)),
                ("ec2.volumes", lambda region=region: self.ebs_volumes(region)),
                ("ec2.vpcs", lambda region=region: self.ec2_vpcs(region)),
                ("ec2.subnets", lambda region=region: self.ec2_subnets(region)),
                ("ec2.security_groups", lambda region=region: self.security_groups(region)),
                ("sns.topics", lambda region=region: self.sns_topics(region)),
                ("sqs.queues", lambda region=region: self.sqs_queues(region)),
                ("dynamodb.tables", lambda region=region: self.dynamodb_tables(region)),
                ("rds.instances", lambda region=region: self.rds_instances(region)),
                ("rds.clusters", lambda region=region: self.rds_clusters(region)),
                ("rds.snapshots", lambda region=region: self.rds_snapshots(region)),
                ("lambda.functions", lambda region=region: self.lambda_functions(region)),
            ]
            for name, collector in regional:
                self._collect_one(result, f"{region}.{name}", collector)
        # S3 lists every bucket of the account once, whatever the selected regions.
        self._collect_one(result, "s3.buckets", self.s3_buckets)
        return result

    @staticmethod
    def _collect_one(result: CollectionResult, name: str, collector: Callable[[], list[Resource]]) -> None:
        try:
            result.extend(collector())
        except Exception as exc:
            result.errors.append(f"{name}: {type(exc).__name__}; check permissions and configuration")

    def _resource(self, resource_type: str, resource_id: str, *, region: str | None = None,
                  zone: str | None = None, name: str | None = None, status: str | None = None,
                  attributes: dict[str, Any] | None = None,
                  raw_data: dict[str, Any]) -> Resource:
        return Resource(
            provider="aws", resource_type=resource_type, resource_id=resource_id,
            name=name, scope_id=self.account_id, region=region, zone=zone, status=status,
            attributes=attributes or {}, raw_data=raw_data,
        )

    def ec2_instances(self, region: str) -> list[Resource]:
        client = self._client("ec2", region_name=region)
        resources: list[Resource] = []
        for page in client.get_paginator("describe_instances").paginate():
            for reservation in page["Reservations"]:
                for instance in reservation["Instances"]:
                    tags = _tags(instance.get("Tags"))
                    resources.append(self._resource(
                        "ec2_instance", instance["InstanceId"], region=region,
                        zone=instance.get("Placement", {}).get("AvailabilityZone"),
                        name=tags.get("Name"), status=instance.get("State", {}).get("Name"),
                        attributes={
                            "instance_type": instance.get("InstanceType"),
                            "private_ip": instance.get("PrivateIpAddress"),
                            "public_ip": instance.get("PublicIpAddress"),
                            "tags": tags,
                        }, raw_data=instance,
                    ))
        return resources

    def _describe(self, region: str, operation: str, key: str, resource_type: str,
                  id_key: str, status_key: str | None = None) -> list[Resource]:
        client = self._client("ec2", region_name=region)
        resources = []
        for page in client.get_paginator(operation).paginate():
            for item in page[key]:
                tags = _tags(item.get("Tags"))
                resources.append(self._resource(
                    resource_type, item[id_key], region=region, name=tags.get("Name"),
                    status=item.get(status_key) if status_key else None,
                    attributes={"tags": tags}, raw_data=item,
                ))
        return resources

    def ebs_volumes(self, region: str) -> list[Resource]:
        return self._describe(region, "describe_volumes", "Volumes", "ebs_volume", "VolumeId", "State")

    def ec2_vpcs(self, region: str) -> list[Resource]:
        return self._describe(region, "describe_vpcs", "Vpcs", "vpc", "VpcId", "State")

    def ec2_subnets(self, region: str) -> list[Resource]:
        return self._describe(region, "describe_subnets", "Subnets", "subnet", "SubnetId", "State")

    def security_groups(self, region: str) -> list[Resource]:
        return self._describe(
            region, "describe_security_groups", "SecurityGroups", "security_group", "GroupId"
        )

    def sns_topics(self, region: str) -> list[Resource]:
        client = self._client("sns", region_name=region)
        resources = []
        for page in client.get_paginator("list_topics").paginate():
            for item in page["Topics"]:
                arn = item["TopicArn"]
                details = _optional(lambda arn=arn: client.get_topic_attributes(TopicArn=arn)["Attributes"], {})
                tags = _optional(lambda arn=arn: client.list_tags_for_resource(ResourceArn=arn)["Tags"], [])
                resources.append(self._resource(
                    "sns_topic", arn, region=region, name=arn.rsplit(":", 1)[-1],
                    attributes={
                        "subscriptions": _number(details.get("SubscriptionsConfirmed")),
                        "subscriptions_pending": _number(details.get("SubscriptionsPending")),
                        "fifo": details.get("FifoTopic") == "true",
                        "tags": _tags(tags),
                    },
                    raw_data={**item, "Attributes": details},
                ))
        return resources

    def sqs_queues(self, region: str) -> list[Resource]:
        client = self._client("sqs", region_name=region)
        resources = []
        for page in client.get_paginator("list_queues").paginate():
            for url in page.get("QueueUrls", []):
                details = _optional(lambda url=url: client.get_queue_attributes(
                    QueueUrl=url, AttributeNames=["All"])["Attributes"], {})
                tags = _optional(lambda url=url: client.list_queue_tags(QueueUrl=url).get("Tags", {}), {})
                redrive = details.get("RedrivePolicy")
                resources.append(self._resource(
                    "sqs_queue", url, region=region, name=url.rsplit("/", 1)[-1],
                    attributes={
                        "dead_letter_queue": (json.loads(redrive).get("deadLetterTargetArn") if redrive else None),
                        "long_polling_seconds": _number(details.get("ReceiveMessageWaitTimeSeconds")),
                        "retention_seconds": _number(details.get("MessageRetentionPeriod")),
                        "visibility_timeout_seconds": _number(details.get("VisibilityTimeout")),
                        "fifo": details.get("FifoQueue") == "true",
                        "tags": tags,
                    },
                    raw_data={"QueueUrl": url, "Attributes": details},
                ))
        return resources


    @staticmethod
    def _iso(value: Any) -> str | None:
        return value.isoformat() if hasattr(value, "isoformat") else value

    def rds_instances(self, region: str) -> list[Resource]:
        client = self._client("rds", region_name=region)
        resources = []
        for page in client.get_paginator("describe_db_instances").paginate():
            for db in page["DBInstances"]:
                resources.append(self._resource(
                    # CloudWatch identifies instances by name; the ARN keeps regions apart.
                    "rds_instance", db["DBInstanceArn"], region=region,
                    zone=db.get("AvailabilityZone"), name=db["DBInstanceIdentifier"],
                    status=db.get("DBInstanceStatus"),
                    attributes={
                        "engine": db.get("Engine"),
                        "engine_version": db.get("EngineVersion"),
                        "instance_class": db.get("DBInstanceClass"),
                        "multi_az": db.get("MultiAZ"),
                        "storage_type": db.get("StorageType"),
                        "allocated_storage_gb": db.get("AllocatedStorage"),
                        "max_allocated_storage_gb": db.get("MaxAllocatedStorage"),
                        "iops": db.get("Iops"),
                        "cluster": db.get("DBClusterIdentifier"),
                        "replica_of": db.get("ReadReplicaSourceDBInstanceIdentifier"),
                        "backup_retention_days": db.get("BackupRetentionPeriod"),
                        "deletion_protection": db.get("DeletionProtection"),
                        "created_at": self._iso(db.get("InstanceCreateTime")),
                        "tags": _tags(db.get("TagList")),
                    },
                    raw_data=db,
                ))
        return resources

    def rds_clusters(self, region: str) -> list[Resource]:
        client = self._client("rds", region_name=region)
        resources = []
        for page in client.get_paginator("describe_db_clusters").paginate():
            for cluster in page["DBClusters"]:
                scaling = cluster.get("ServerlessV2ScalingConfiguration") or {}
                resources.append(self._resource(
                    "rds_cluster", cluster["DBClusterArn"], region=region,
                    name=cluster["DBClusterIdentifier"], status=cluster.get("Status"),
                    attributes={
                        "engine": cluster.get("Engine"),
                        "engine_version": cluster.get("EngineVersion"),
                        "engine_mode": cluster.get("EngineMode"),
                        "members": len(cluster.get("DBClusterMembers") or []),
                        "serverless_v2_capacity": [scaling.get("MinCapacity"), scaling.get("MaxCapacity")] if scaling else None,
                        "storage_type": cluster.get("StorageType"),
                        "multi_az": cluster.get("MultiAZ"),
                        "backup_retention_days": cluster.get("BackupRetentionPeriod"),
                        "deletion_protection": cluster.get("DeletionProtection"),
                        "created_at": self._iso(cluster.get("ClusterCreateTime")),
                        "tags": _tags(cluster.get("TagList")),
                    },
                    raw_data=cluster,
                ))
        return resources

    def rds_snapshots(self, region: str) -> list[Resource]:
        """Manual snapshots only: automated ones expire with the retention period."""
        client = self._client("rds", region_name=region)
        resources = []
        for operation, key, id_key, source_key in (
            ("describe_db_snapshots", "DBSnapshots", "DBSnapshotArn", "DBInstanceIdentifier"),
            ("describe_db_cluster_snapshots", "DBClusterSnapshots", "DBClusterSnapshotArn", "DBClusterIdentifier"),
        ):
            for page in client.get_paginator(operation).paginate(SnapshotType="manual"):
                for snapshot in page[key]:
                    resources.append(self._resource(
                        "rds_snapshot", snapshot[id_key], region=region,
                        name=snapshot.get("DBSnapshotIdentifier") or snapshot.get("DBClusterSnapshotIdentifier"),
                        status=snapshot.get("Status"),
                        attributes={
                            "source": snapshot.get(source_key),
                            "cluster_snapshot": key == "DBClusterSnapshots",
                            "engine": snapshot.get("Engine"),
                            "engine_version": snapshot.get("EngineVersion"),
                            "allocated_storage_gb": snapshot.get("AllocatedStorage"),
                            "encrypted": snapshot.get("Encrypted"),
                            "created_at": self._iso(snapshot.get("SnapshotCreateTime")),
                            "tags": _tags(snapshot.get("TagList")),
                        },
                        raw_data=snapshot,
                    ))
        return resources

    def lambda_functions(self, region: str) -> list[Resource]:
        client = self._client("lambda", region_name=region)
        resources = []
        for page in client.get_paginator("list_functions").paginate():
            for function in page["Functions"]:
                arn, name = function["FunctionArn"], function["FunctionName"]
                tags = _optional(lambda arn=arn: client.list_tags(Resource=arn).get("Tags", {}), {})
                provisioned = _optional(lambda name=name: sum(
                    item.get("AllocatedProvisionedConcurrentExecutions") or 0
                    for item in client.list_provisioned_concurrency_configs(
                        FunctionName=name).get("ProvisionedConcurrencyConfigs", [])
                ))
                resources.append(self._resource(
                    "lambda_function", arn, region=region, name=name, status=function.get("State"),
                    attributes={
                        "runtime": function.get("Runtime"),
                        "memory_mb": function.get("MemorySize"),
                        "timeout_seconds": function.get("Timeout"),
                        "architectures": function.get("Architectures") or [],
                        "package_type": function.get("PackageType"),
                        "ephemeral_storage_mb": (function.get("EphemeralStorage") or {}).get("Size"),
                        # Provisioned concurrency is billed even without invocations.
                        "provisioned_concurrency": provisioned,
                        "last_modified": function.get("LastModified"),
                        "tags": tags,
                    },
                    raw_data=function,
                ))
        return resources

    def s3_buckets(self) -> list[Resource]:
        client = self._client("s3")
        resources = []
        for bucket in client.list_buckets().get("Buckets", []):
            name = bucket["Name"]
            # An empty LocationConstraint means us-east-1.
            region = _optional(lambda name=name: client.get_bucket_location(Bucket=name).get("LocationConstraint")) or "us-east-1"
            lifecycle = _optional(lambda name=name: client.get_bucket_lifecycle_configuration(Bucket=name).get("Rules"), [])
            versioning = _optional(lambda name=name: client.get_bucket_versioning(Bucket=name).get("Status"))
            tags = _optional(lambda name=name: client.get_bucket_tagging(Bucket=name).get("TagSet"), [])
            tiering = _optional(lambda name=name: client.list_bucket_intelligent_tiering_configurations(
                Bucket=name).get("IntelligentTieringConfigurationList"), [])
            resources.append(self._resource(
                "s3_bucket", name, region=region, name=name,
                attributes={
                    "lifecycle_rules": len(lifecycle or []),
                    "aborts_incomplete_uploads": any("AbortIncompleteMultipartUpload" in rule for rule in lifecycle or []),
                    "versioning": versioning == "Enabled",
                    "intelligent_tiering": bool(tiering),
                    "created_at": bucket["CreationDate"].isoformat() if bucket.get("CreationDate") else None,
                    "tags": _tags(tags),
                },
                raw_data={"Name": name, "Region": region, "LifecycleRules": lifecycle or [],
                          "Versioning": versioning, "IntelligentTiering": tiering or []},
            ))
        return resources

    def dynamodb_tables(self, region: str) -> list[Resource]:
        client = self._client("dynamodb", region_name=region)
        resources = []
        for page in client.get_paginator("list_tables").paginate():
            for table_name in page.get("TableNames", []):
                table = client.describe_table(TableName=table_name)["Table"]
                throughput = table.get("ProvisionedThroughput") or {}
                tags = _optional(lambda arn=table["TableArn"]: client.list_tags_of_resource(ResourceArn=arn)["Tags"], [])
                resources.append(self._resource(
                    "dynamodb_table", table["TableArn"], region=region,
                    name=table_name, status=table.get("TableStatus"),
                    attributes={
                        # Tables created as provisioned may report no BillingModeSummary.
                        "billing_mode": table.get("BillingModeSummary", {}).get("BillingMode") or "PROVISIONED",
                        "item_count": table.get("ItemCount"),
                        "table_size_bytes": table.get("TableSizeBytes"),
                        "provisioned_read": throughput.get("ReadCapacityUnits"),
                        "provisioned_write": throughput.get("WriteCapacityUnits"),
                        "tags": _tags(tags),
                    },
                    raw_data=table,
                ))
        return resources
