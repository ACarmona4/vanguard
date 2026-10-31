"""Provider-neutral resource groups used by cross-cloud filters."""

RESOURCE_GROUPS = {
    "instances": ("ec2_instance", "compute_instance"),
    "databases": ("dynamodb_table", "cloudsql_instance", "rds_instance", "rds_cluster"),
    "storage": ("ebs_volume", "persistent_disk", "s3_bucket", "storage_bucket", "rds_snapshot"),
    "network": ("vpc", "subnet", "security_group", "vpc_network", "subnetwork", "firewall_rule", "ip_address"),
    "serverless": ("lambda_function", "cloud_run_service", "cloud_function"),
    "analytics": ("bigquery_dataset", "bigquery_table"),
    "messaging": ("sqs_queue", "sns_topic", "pubsub_topic", "pubsub_subscription"),
    "identity": ("service_account", "project_iam_policy"),
}


def resource_types_for(value: str | None) -> tuple[str, ...] | None:
    return RESOURCE_GROUPS.get(value) if value else None
