from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from ..models import CollectionResult, Resource


def _tags(tags: list[dict[str, str]] | None) -> dict[str, str]:
    return {tag["Key"]: tag["Value"] for tag in tags or []}


class AWSCollector:
    def __init__(self, profile: str | None = None, regions: Iterable[str] | None = None):
        import boto3

        self.session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        self.account_id = self.session.client("sts").get_caller_identity()["Account"]
        requested = [region.strip() for region in regions or [] if region.strip()]
        self.regions = requested or ([self.session.region_name] if self.session.region_name else [])
        if not self.regions:
            ec2 = self.session.client("ec2", region_name="us-east-1")
            self.regions = sorted(item["RegionName"] for item in ec2.describe_regions()["Regions"])

    def collect(self) -> CollectionResult:
        result = CollectionResult()
        global_collectors: list[tuple[str, Callable[[], list[Resource]]]] = [
            ("iam.users", self.iam_users),
            ("iam.roles", self.iam_roles),
        ]
        for name, collector in global_collectors:
            self._collect_one(result, name, collector)
        for region in self.regions:
            regional: list[tuple[str, Callable[[], list[Resource]]]] = [
                ("ec2.instances", lambda region=region: self.ec2_instances(region)),
                ("ec2.vpcs", lambda region=region: self.ec2_vpcs(region)),
                ("ec2.subnets", lambda region=region: self.ec2_subnets(region)),
                ("ec2.security_groups", lambda region=region: self.security_groups(region)),
                ("sns.topics", lambda region=region: self.sns_topics(region)),
                ("sqs.queues", lambda region=region: self.sqs_queues(region)),
            ]
            for name, collector in regional:
                self._collect_one(result, f"{region}.{name}", collector)
        return result

    @staticmethod
    def _collect_one(result: CollectionResult, name: str, collector: Callable[[], list[Resource]]) -> None:
        try:
            result.extend(collector())
        except Exception as exc:
            result.errors.append(f"{name}: {type(exc).__name__}: {exc}")

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
        client = self.session.client("ec2", region_name=region)
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
        client = self.session.client("ec2", region_name=region)
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

    def ec2_vpcs(self, region: str) -> list[Resource]:
        return self._describe(region, "describe_vpcs", "Vpcs", "vpc", "VpcId", "State")

    def ec2_subnets(self, region: str) -> list[Resource]:
        return self._describe(region, "describe_subnets", "Subnets", "subnet", "SubnetId", "State")

    def security_groups(self, region: str) -> list[Resource]:
        return self._describe(
            region, "describe_security_groups", "SecurityGroups", "security_group", "GroupId"
        )

    def iam_users(self) -> list[Resource]:
        client = self.session.client("iam")
        resources = []
        for page in client.get_paginator("list_users").paginate():
            for item in page["Users"]:
                resources.append(self._resource(
                    "iam_user", item["Arn"], name=item["UserName"], raw_data=item
                ))
        return resources

    def iam_roles(self) -> list[Resource]:
        client = self.session.client("iam")
        resources = []
        for page in client.get_paginator("list_roles").paginate():
            for item in page["Roles"]:
                resources.append(self._resource(
                    "iam_role", item["Arn"], name=item["RoleName"], raw_data=item
                ))
        return resources

    def sns_topics(self, region: str) -> list[Resource]:
        client = self.session.client("sns", region_name=region)
        resources = []
        for page in client.get_paginator("list_topics").paginate():
            for item in page["Topics"]:
                arn = item["TopicArn"]
                resources.append(self._resource(
                    "sns_topic", arn, region=region, name=arn.rsplit(":", 1)[-1], raw_data=item
                ))
        return resources

    def sqs_queues(self, region: str) -> list[Resource]:
        client = self.session.client("sqs", region_name=region)
        resources = []
        for page in client.get_paginator("list_queues").paginate():
            resources.extend(self._resource(
                "sqs_queue", url, region=region, name=url.rsplit("/", 1)[-1],
                raw_data={"QueueUrl": url},
            ) for url in page.get("QueueUrls", []))
        return resources


def check_aws(profile: str | None = None) -> str:
    import boto3

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    identity = session.client("sts").get_caller_identity()
    return f"AWS disponible: account={identity['Account']}, arn={identity['Arn']}"
