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
MEMORY = Metric("memory_percent", "Memoria", "%", agent=True)
FILESYSTEM = Metric("filesystem_percent", "Sistema de archivos (máximo)", "%", agent=True)

AWS = {
    "ec2_instance": ("AWS/EC2", "InstanceId", (
        CPU,
        Metric("memory_percent", "Memoria", "%", "MemoryUsedPercent", agent=True, namespace="Vanguard/HostMetrics"),
        Metric("filesystem_percent", "Sistema de archivos (máximo)", "%", "FilesystemUsedPercent", "Maximum", agent=True, namespace="Vanguard/HostMetrics"),
        Metric("network_in_bytes_per_second", "Red entrante", "B/s", "NetworkIn", "Sum", 1 / 300),
        Metric("network_out_bytes_per_second", "Red saliente", "B/s", "NetworkOut", "Sum", 1 / 300),
        Metric("disk_read_bytes_per_second", "Lectura de disco local", "B/s", "DiskReadBytes", "Sum", 1 / 300),
        Metric("disk_write_bytes_per_second", "Escritura de disco local", "B/s", "DiskWriteBytes", "Sum", 1 / 300),
    )),
    "ebs_volume": ("AWS/EBS", "VolumeId", (
        Metric("disk_read_bytes_per_second", "Lectura de disco", "B/s", "VolumeReadBytes", "Sum", 1 / 300),
        Metric("disk_write_bytes_per_second", "Escritura de disco", "B/s", "VolumeWriteBytes", "Sum", 1 / 300),
        Metric("disk_queue", "Operaciones pendientes", "operaciones", "VolumeQueueLength"),
    )),
    "dynamodb_table": ("AWS/DynamoDB", "TableName", (
        Metric("read_units_per_second", "Lecturas consumidas", "unidades/s", "ConsumedReadCapacityUnits", "Sum", 1 / 300),
        Metric("write_units_per_second", "Escrituras consumidas", "unidades/s", "ConsumedWriteCapacityUnits", "Sum", 1 / 300),
    )),
    "sqs_queue": ("AWS/SQS", "QueueName", (
        Metric("queued_messages", "Mensajes pendientes", "mensajes", "ApproximateNumberOfMessagesVisible"),
        Metric("oldest_message_seconds", "Antigüedad del mensaje más antiguo", "s", "ApproximateAgeOfOldestMessage", "Maximum"),
    )),
    "sns_topic": ("AWS/SNS", "TopicName", (
        Metric("published_per_second", "Publicaciones", "mensajes/s", "NumberOfMessagesPublished", "Sum", 1 / 300),
        Metric("failed_per_second", "Entregas fallidas", "mensajes/s", "NumberOfNotificationsFailed", "Sum", 1 / 300),
    )),
}

# Monitored resource type, identifier label, metrics. DELTA values are aligned to rates.
GCP = {
    "compute_instance": ("gce_instance", "instance_id", (
        Metric("cpu_percent", "CPU", "%", "compute.googleapis.com/instance/cpu/utilization", scale=100),
        MEMORY, FILESYSTEM,
        Metric("network_in_bytes_per_second", "Red entrante", "B/s", "compute.googleapis.com/instance/network/received_bytes_count", "rate"),
        Metric("network_out_bytes_per_second", "Red saliente", "B/s", "compute.googleapis.com/instance/network/sent_bytes_count", "rate"),
        Metric("disk_read_bytes_per_second", "Lectura de disco", "B/s", "compute.googleapis.com/instance/disk/read_bytes_count", "rate"),
        Metric("disk_write_bytes_per_second", "Escritura de disco", "B/s", "compute.googleapis.com/instance/disk/write_bytes_count", "rate"),
    )),
    "cloudsql_instance": ("cloudsql_database", "database_id", (
        Metric("cpu_percent", "CPU", "%", "cloudsql.googleapis.com/database/cpu/utilization", scale=100),
        Metric("memory_percent", "Memoria", "%", "cloudsql.googleapis.com/database/memory/utilization", scale=100),
        Metric("filesystem_percent", "Disco utilizado", "%", "cloudsql.googleapis.com/database/disk/utilization", scale=100),
        Metric("connections", "Conexiones PostgreSQL", "conexiones", "cloudsql.googleapis.com/database/postgresql/num_backends"),
    )),
    "pubsub_topic": ("pubsub_topic", "topic_id", (
        Metric("retained_bytes", "Mensajes retenidos", "B", "pubsub.googleapis.com/topic/retained_bytes"),
    )),
    "pubsub_subscription": ("pubsub_subscription", "subscription_id", (
        Metric("queued_messages", "Mensajes sin confirmar", "mensajes", "pubsub.googleapis.com/subscription/num_undelivered_messages"),
        Metric("oldest_message_seconds", "Antigüedad del mensaje sin confirmar", "s", "pubsub.googleapis.com/subscription/oldest_unacked_message_age", "max"),
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
