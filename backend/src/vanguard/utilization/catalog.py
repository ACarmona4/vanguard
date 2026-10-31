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
    # Published only for some configurations; never reported as missing telemetry.
    optional: bool = False
    # Published only while the resource has traffic; an idle resource reports nothing.
    activity: bool = False


CPU = Metric("cpu_percent", "CPU", "%", "CPUUtilization")

AWS = {
    "ec2_instance": ("AWS/EC2", "InstanceId", (
        CPU,
        Metric("network_in_bytes_per_second", "Network in", "B/s", "NetworkIn", "Sum", 1 / 300),
        Metric("network_out_bytes_per_second", "Network out", "B/s", "NetworkOut", "Sum", 1 / 300),
        # CloudWatch reports DiskRead/WriteBytes only for instance store volumes, not EBS-only instances.
        Metric("disk_read_bytes_per_second", "Local disk read", "B/s", "DiskReadBytes", "Sum", 1 / 300, optional=True),
        Metric("disk_write_bytes_per_second", "Local disk write", "B/s", "DiskWriteBytes", "Sum", 1 / 300, optional=True),
    )),
    "ebs_volume": ("AWS/EBS", "VolumeId", (
        Metric("disk_read_bytes_per_second", "Disk read", "B/s", "VolumeReadBytes", "Sum", 1 / 300),
        Metric("disk_write_bytes_per_second", "Disk write", "B/s", "VolumeWriteBytes", "Sum", 1 / 300),
        Metric("disk_queue", "Pending operations", "operations", "VolumeQueueLength"),
    )),
    # CloudWatch stops publishing these when DynamoDB, SQS or SNS see no requests.
    "dynamodb_table": ("AWS/DynamoDB", "TableName", (
        Metric("read_units_per_second", "Consumed reads", "units/s", "ConsumedReadCapacityUnits", "Sum", 1 / 300, activity=True),
        Metric("write_units_per_second", "Consumed writes", "units/s", "ConsumedWriteCapacityUnits", "Sum", 1 / 300, activity=True),
        # Reported only for provisioned tables; on-demand tables have no reserved capacity.
        Metric("provisioned_read_units", "Provisioned reads", "units/s", "ProvisionedReadCapacityUnits", optional=True),
        Metric("provisioned_write_units", "Provisioned writes", "units/s", "ProvisionedWriteCapacityUnits", optional=True),
    )),
    "sqs_queue": ("AWS/SQS", "QueueName", (
        Metric("queued_messages", "Pending messages", "messages", "ApproximateNumberOfMessagesVisible", activity=True),
        Metric("oldest_message_seconds", "Oldest message age", "s", "ApproximateAgeOfOldestMessage", "Maximum", activity=True),
        Metric("messages_sent_per_second", "Messages sent", "messages/s", "NumberOfMessagesSent", "Sum", 1 / 300, activity=True),
        Metric("messages_received_per_second", "Messages received", "messages/s", "NumberOfMessagesReceived", "Sum", 1 / 300, activity=True),
        Metric("empty_receives_per_second", "Empty receives", "requests/s", "NumberOfEmptyReceives", "Sum", 1 / 300, activity=True),
    )),
    "rds_instance": ("AWS/RDS", "DBInstanceIdentifier", (
        CPU,
        Metric("freeable_memory_bytes", "Free memory", "B", "FreeableMemory"),
        Metric("connections", "Database connections", "connections", "DatabaseConnections"),
        # Aurora keeps storage and I/O at cluster level, so instances may not report them.
        Metric("free_storage_bytes", "Free storage", "B", "FreeStorageSpace", optional=True),
        Metric("read_iops", "Read IOPS", "operations/s", "ReadIOPS", optional=True),
        Metric("write_iops", "Write IOPS", "operations/s", "WriteIOPS", optional=True),
    )),
    # Serverless functions report only while invoked, so every metric is an activity metric.
    "lambda_function": ("AWS/Lambda", "FunctionName", (
        Metric("invocations_per_second", "Invocations", "requests/s", "Invocations", "Sum", 1 / 300, activity=True),
        Metric("errors_per_second", "Errors", "requests/s", "Errors", "Sum", 1 / 300, activity=True),
        Metric("throttles_per_second", "Throttles", "requests/s", "Throttles", "Sum", 1 / 300, activity=True),
        Metric("duration_ms", "Duration", "ms", "Duration", activity=True),
        Metric("concurrent_executions", "Concurrent executions", "executions", "ConcurrentExecutions", "Maximum", activity=True),
    )),
    "sns_topic": ("AWS/SNS", "TopicName", (
        Metric("published_per_second", "Published messages", "messages/s", "NumberOfMessagesPublished", "Sum", 1 / 300, activity=True),
        Metric("failed_per_second", "Failed deliveries", "messages/s", "NumberOfNotificationsFailed", "Sum", 1 / 300, activity=True),
    )),
}

# Monitored resource type, identifier label, metrics. DELTA values are aligned to rates.
GCP = {
    "compute_instance": ("gce_instance", "instance_id", (
        Metric("cpu_percent", "CPU", "%", "compute.googleapis.com/instance/cpu/utilization", scale=100),
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
    "cloud_run_service": ("cloud_run_revision", "service_name", (
        Metric("requests_per_second", "Requests", "requests/s", "run.googleapis.com/request_count", "rate", activity=True),
        Metric("instances", "Instances", "instances", "run.googleapis.com/container/instance_count", activity=True),
        Metric("billable_instance_seconds", "Billable instance time", "s/s",
               "run.googleapis.com/container/billable_instance_time", "rate", activity=True),
    )),
    "cloud_function": ("cloud_function", "function_name", (
        Metric("invocations_per_second", "Invocations", "requests/s",
               "cloudfunctions.googleapis.com/function/execution_count", "rate", activity=True),
    )),
    "pubsub_topic": ("pubsub_topic", "topic_id", (
        Metric("retained_bytes", "Retained messages", "B", "pubsub.googleapis.com/topic/retained_bytes"),
        Metric("published_per_second", "Published messages", "requests/s",
               "pubsub.googleapis.com/topic/send_message_operation_count", "rate", activity=True),
    )),
    "pubsub_subscription": ("pubsub_subscription", "subscription_id", (
        Metric("queued_messages", "Unacknowledged messages", "messages", "pubsub.googleapis.com/subscription/num_undelivered_messages"),
        Metric("oldest_message_seconds", "Oldest unacknowledged message age", "s", "pubsub.googleapis.com/subscription/oldest_unacked_message_age", "max"),
        Metric("acked_per_second", "Acknowledged messages", "messages/s",
               "pubsub.googleapis.com/subscription/ack_message_count", "rate", activity=True),
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
    return tuple(
        str(resource.get(key) or "")
        for key in ("owner_id", "provider", "scope_id", "region", "resource_type", "resource_id")
    )


IDENTITY_LABELS = ("owner_id", "provider", "scope_id", "region", "resource_type", "resource_id")
