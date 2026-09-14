from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..models import CollectionResult, Resource


def _message_to_dict(message: Any) -> dict[str, Any]:
    from google.protobuf.json_format import MessageToDict

    protobuf = getattr(message, "_pb", message)
    return MessageToDict(protobuf, preserving_proto_field_name=True)


def _last_path_part(value: str | None) -> str | None:
    return value.rsplit("/", 1)[-1] if value else None


class GCPCollector:
    def __init__(self, project_id: str):
        self.project_id = project_id

    def collect(self) -> CollectionResult:
        result = CollectionResult()
        collectors: list[tuple[str, Callable[[], list[Resource]]]] = [
            ("compute.instances", self.instances),
            ("compute.networks", self.networks),
            ("compute.subnetworks", self.subnetworks),
            ("compute.firewalls", self.firewalls),
            ("pubsub.topics", self.pubsub_topics),
            ("pubsub.subscriptions", self.pubsub_subscriptions),
            ("iam.service_accounts", self.service_accounts),
            ("iam.project_policy", self.project_policy),
        ]
        for name, collector in collectors:
            try:
                result.extend(collector())
            except Exception as exc:  # one unavailable API must not hide other assets
                result.errors.append(f"{name}: {type(exc).__name__}: {exc}")
        return result

    def instances(self) -> list[Resource]:
        from google.cloud import compute_v1

        resources: list[Resource] = []
        client = compute_v1.InstancesClient()
        for scope, response in client.aggregated_list(project=self.project_id):
            for instance in response.instances or []:
                interface = instance.network_interfaces[0] if instance.network_interfaces else None
                public_ip = None
                if interface and interface.access_configs:
                    public_ip = interface.access_configs[0].nat_i_p
                resources.append(Resource(
                    provider="gcp",
                    resource_type="compute_instance",
                    resource_id=str(instance.id),
                    name=instance.name,
                    scope_id=self.project_id,
                    zone=_last_path_part(instance.zone) or _last_path_part(scope),
                    status=instance.status,
                    attributes={
                        "machine_type": _last_path_part(instance.machine_type),
                        "private_ip": interface.network_i_p if interface else None,
                        "public_ip": public_ip,
                    },
                    raw_data=_message_to_dict(instance),
                ))
        return resources

    def networks(self) -> list[Resource]:
        from google.cloud import compute_v1

        return [Resource(
            provider="gcp", resource_type="vpc_network", resource_id=str(network.id),
            name=network.name, scope_id=self.project_id, raw_data=_message_to_dict(network),
        ) for network in compute_v1.NetworksClient().list(project=self.project_id)]

    def subnetworks(self) -> list[Resource]:
        from google.cloud import compute_v1

        resources: list[Resource] = []
        for scope, response in compute_v1.SubnetworksClient().aggregated_list(project=self.project_id):
            for subnet in response.subnetworks or []:
                resources.append(Resource(
                    provider="gcp", resource_type="subnetwork", resource_id=str(subnet.id),
                    name=subnet.name, scope_id=self.project_id,
                    region=_last_path_part(subnet.region) or _last_path_part(scope),
                    attributes={"cidr": subnet.ip_cidr_range}, raw_data=_message_to_dict(subnet),
                ))
        return resources

    def firewalls(self) -> list[Resource]:
        from google.cloud import compute_v1

        return [Resource(
            provider="gcp", resource_type="firewall_rule", resource_id=str(rule.id),
            name=rule.name, scope_id=self.project_id,
            status="DISABLED" if rule.disabled else "ENABLED", raw_data=_message_to_dict(rule),
        ) for rule in compute_v1.FirewallsClient().list(project=self.project_id)]

    def pubsub_topics(self) -> list[Resource]:
        from google.cloud import pubsub_v1

        parent = f"projects/{self.project_id}"
        return [Resource(
            provider="gcp", resource_type="pubsub_topic", resource_id=topic.name,
            name=_last_path_part(topic.name), scope_id=self.project_id, raw_data=_message_to_dict(topic),
        ) for topic in pubsub_v1.PublisherClient().list_topics(request={"project": parent})]

    def pubsub_subscriptions(self) -> list[Resource]:
        from google.cloud import pubsub_v1

        parent = f"projects/{self.project_id}"
        return [Resource(
            provider="gcp", resource_type="pubsub_subscription", resource_id=subscription.name,
            name=_last_path_part(subscription.name), scope_id=self.project_id,
            status=str(subscription.state.name),
            attributes={"topic": subscription.topic, "filter": subscription.filter},
            raw_data=_message_to_dict(subscription),
        ) for subscription in pubsub_v1.SubscriberClient().list_subscriptions(
            request={"project": parent}
        )]

    def service_accounts(self) -> list[Resource]:
        from google.cloud import iam_admin_v1

        return [Resource(
            provider="gcp", resource_type="service_account", resource_id=account.unique_id,
            name=account.email, scope_id=self.project_id, raw_data=_message_to_dict(account),
        ) for account in iam_admin_v1.IAMClient().list_service_accounts(
            request={"name": f"projects/{self.project_id}"}
        )]

    def project_policy(self) -> list[Resource]:
        from google.cloud import resourcemanager_v3
        from google.iam.v1 import iam_policy_pb2

        policy = resourcemanager_v3.ProjectsClient().get_iam_policy(
            request=iam_policy_pb2.GetIamPolicyRequest(resource=f"projects/{self.project_id}")
        )
        return [Resource(
            provider="gcp", resource_type="project_iam_policy",
            resource_id=f"projects/{self.project_id}/iamPolicy", name="iamPolicy",
            scope_id=self.project_id, raw_data=_message_to_dict(policy),
        )]


def check_gcp(project_id: str) -> str:
    import google.auth
    from google.auth.transport.requests import Request
    from google.cloud import resourcemanager_v3

    credentials, detected_project = google.auth.default()
    credentials.refresh(Request())
    project = resourcemanager_v3.ProjectsClient().get_project(name=f"projects/{project_id}")
    return (
        f"GCP disponible: project={project.project_id}, state={project.state.name}, "
        f"credential_project={detected_project or 'not-set'}"
    )

