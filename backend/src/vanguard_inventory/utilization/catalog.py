"""Explicit capabilities. Logical resources never acquire fictitious CPU metrics."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    cloud_name: str = ""
    statistic: str = "Average"
    scale: float = 1
    agent: bool = False
    namespace: str | None = None


CPU = Metric("cpu_percent", "CPU", "%", "CPUUtilization")
MEMORY = Metric("memory_percent", "Memory", "%", agent=True)
FILESYSTEM = Metric("filesystem_percent", "File system (maximum)", "%", agent=True)

AWS = {
    "ec2_instance": ("AWS/EC2", "InstanceId", (
        CPU,
        Metric("memory_percent", "Memory", "%", "MemoryUsedPercent", agent=True, namespace="Vanguard/HostMetrics"),
        Metric("filesystem_percent", "File system (maximum)", "%", "FilesystemUsedPercent", "Maximum", agent=True, namespace="Vanguard/HostMetrics"),
        Metric("network_in_bytes_per_second", "Network in", "B/s", "NetworkIn", "Sum", 1 / 300),
        Metric("network_out_bytes_per_second", "Network out", "B/s", "NetworkOut", "Sum", 1 / 300),
        Metric("disk_read_bytes_per_second", "Local disk read", "B/s", "DiskReadBytes", "Sum", 1 / 300),
        Metric("disk_write_bytes_per_second", "Local disk write", "B/s", "DiskWriteBytes", "Sum", 1 / 300),
    )),
    "ebs_volume": ("AWS/EBS", "VolumeId", (
        Metric("disk_read_bytes_per_second", "Disk read", "B/s", "VolumeReadBytes", "Sum", 1 / 300),
        Metric("disk_write_bytes_per_second", "Disk write", "B/s", "VolumeWriteBytes", "Sum", 1 / 300),
        Metric("disk_queue", "Pending operations", "operations", "VolumeQueueLength"),
    )),
    "dynamodb_table": ("AWS/DynamoDB", "TableName", (
        Metric("read_units_per_second", "Consumed reads", "units/s", "ConsumedReadCapacityUnits", "Sum", 1 / 300),
        Metric("write_units_per_second", "Consumed writes", "units/s", "ConsumedWriteCapacityUnits", "Sum", 1 / 300),
    )),
    "sqs_queue": ("AWS/SQS", "QueueName", (
        Metric("queued_messages", "Pending messages", "messages", "ApproximateNumberOfMessagesVisible"),
        Metric("oldest_message_seconds", "Oldest message age", "s", "ApproximateAgeOfOldestMessage", "Maximum"),
    )),
    "sns_topic": ("AWS/SNS", "TopicName", (
        Metric("published_per_second", "Published messages", "messages/s", "NumberOfMessagesPublished", "Sum", 1 / 300),
        Metric("failed_per_second", "Failed deliveries", "messages/s", "NumberOfNotificationsFailed", "Sum", 1 / 300),
    )),
}

# Monitored resource type, identifier label, metrics. DELTA values are aligned to rates.
GCP = {
    "compute_instance": ("gce_instance", "instance_id", (
        Metric("cpu_percent", "CPU", "%", "compute.googleapis.com/instance/cpu/utilization", scale=100),
        MEMORY, FILESYSTEM,
        Metric("network_in_bytes_per_second", "Network in", "B/s", "compute.googleapis.com/instance/network/received_bytes_count", "rate"),
        Metric("network_out_bytes_per_second", "Network out", "B/s", "compute.googleapis.com/instance/network/sent_bytes_count", "rate"),
        Metric("disk_read_bytes_per_second", "Disk read", "B/s", "compute.googleapis.com/instance/disk/read_bytes_count", "rate"),
        Metric("disk_write_bytes_per_second", "Disk write", "B/s", "compute.googleapis.com/instance/disk/write_bytes_count", "rate"),
    )),
    "cloudsql_instance": ("cloudsql_database", "database_id", (
        Metric("cpu_percent", "CPU", "%", "cloudsql.googleapis.com/database/cpu/utilization", scale=100),
        Metric("memory_percent", "Memory", "%", "cloudsql.googleapis.com/database/memory/utilization", scale=100),
        Metric("filesystem_percent", "Disk used", "%", "cloudsql.googleapis.com/database/disk/utilization", scale=100),
        Metric("connections", "PostgreSQL connections", "connections", "cloudsql.googleapis.com/database/postgresql/num_backends"),
    )),
    "pubsub_topic": ("pubsub_topic", "topic_id", (
        Metric("retained_bytes", "Retained messages", "B", "pubsub.googleapis.com/topic/retained_bytes"),
    )),
    "pubsub_subscription": ("pubsub_subscription", "subscription_id", (
        Metric("queued_messages", "Unacknowledged messages", "messages", "pubsub.googleapis.com/subscription/num_undelivered_messages"),
        Metric("oldest_message_seconds", "Oldest unacknowledged message age", "s", "pubsub.googleapis.com/subscription/oldest_unacked_message_age", "max"),
    )),
}


def metrics_for(resource: dict) -> tuple[Metric, ...]:
    catalog = {"aws": AWS, "gcp": GCP}.get(resource["provider"], {})
    entry = catalog.get(resource["resource_type"])
    if not entry:
        return ()
    metrics = entry[2]
    if resource["resource_type"] == "cloudsql_instance":
        version = resource.get("attributes", {}).get("database_version") or ""
        if not version.startswith("POSTGRES"):
            metrics = tuple(metric for metric in metrics if metric.key != "connections")
    return metrics


def identity(resource: dict) -> tuple[str, ...]:
    return tuple(str(resource.get(key) or "") for key in ("provider", "scope_id", "region", "resource_type", "resource_id"))


IDENTITY_LABELS = ("provider", "scope_id", "region", "resource_type", "resource_id")
