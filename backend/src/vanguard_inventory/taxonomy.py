"""Provider-neutral resource groups used by cross-cloud filters."""

RESOURCE_GROUPS = {
    "instances": ("ec2_instance", "compute_instance"),
    "databases": ("dynamodb_table", "cloudsql_instance"),
    "storage": ("ebs_volume",),
    "network": ("vpc", "subnet", "security_group", "vpc_network", "subnetwork", "firewall_rule"),
    "messaging": ("sqs_queue", "sns_topic", "pubsub_topic", "pubsub_subscription"),
    "identity": ("service_account", "project_iam_policy"),
}


def resource_types_for(value: str | None) -> tuple[str, ...] | None:
    return RESOURCE_GROUPS.get(value) if value else None
