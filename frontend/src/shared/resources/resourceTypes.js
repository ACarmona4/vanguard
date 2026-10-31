export const resourceGroups = [
  { value: "instances", label: "Instances", types: ["ec2_instance", "compute_instance"] },
  { value: "databases", label: "Databases", types: ["dynamodb_table", "cloudsql_instance", "rds_instance", "rds_cluster"] },
  { value: "storage", label: "Storage", types: ["ebs_volume", "persistent_disk", "s3_bucket", "storage_bucket", "rds_snapshot"] },
  { value: "network", label: "Network", types: ["vpc", "subnet", "security_group", "vpc_network", "subnetwork", "firewall_rule", "ip_address"] },
  { value: "serverless", label: "Serverless", types: ["lambda_function", "cloud_run_service", "cloud_function"] },
  { value: "analytics", label: "Analytics", types: ["bigquery_dataset", "bigquery_table"] },
  { value: "messaging", label: "Messaging", types: ["sqs_queue", "sns_topic", "pubsub_topic", "pubsub_subscription"] },
  { value: "identity", label: "Identity", types: ["service_account", "project_iam_policy"] },
];

export const utilizationTypesByProvider = {
  aws: ["ec2_instance", "ebs_volume", "dynamodb_table", "rds_instance", "lambda_function", "sqs_queue", "sns_topic"],
  gcp: ["compute_instance", "cloudsql_instance", "cloud_run_service", "cloud_function", "pubsub_topic", "pubsub_subscription"],
};

export const utilizationGroups = resourceGroups.filter((group) =>
  group.types.some((type) =>
    Object.values(utilizationTypesByProvider).some((types) => types.includes(type)),
  ),
).filter((group) => group.value !== "network" && group.value !== "identity");

export function resourceGroupName(value) {
  return resourceGroups.find((group) => group.value === value)?.label;
}

export function groupedOptions(typeCounts) {
  return resourceGroups
    .map((group) => ({
      value: group.value,
      count: typeCounts
        .filter((item) => group.types.includes(item.value))
        .reduce((total, item) => total + item.count, 0),
    }))
    .filter((item) => item.count > 0);
}
