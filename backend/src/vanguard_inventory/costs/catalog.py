"""Normalize provider service names into stable infrastructure categories."""

CATEGORIES = (
    "Compute",
    "Containers",
    "Databases",
    "Storage",
    "Network",
    "Serverless",
    "Messaging",
    "Observability",
    "Security",
    "Other",
)

_RULES = {
    "Compute": ("compute", "ec2", "virtual machine"),
    "Containers": ("kubernetes", "container", "eks", "ecs", "fargate"),
    "Databases": ("sql", "database", "dynamodb", "rds", "spanner", "bigtable", "firestore"),
    "Storage": ("storage", "simple storage", "elastic block", "backup", "snapshot"),
    "Network": ("network", "cloudfront", "route 53", "load balanc", "data transfer", "cdn"),
    "Serverless": ("lambda", "cloud run", "app engine", "functions"),
    "Messaging": ("pub/sub", "pubsub", "queue", "notification", "sqs", "sns", "kinesis"),
    "Observability": ("cloudwatch", "monitoring", "logging", "trace"),
    "Security": ("security", "guardduty", "kms", "key management", "secret manager"),
}


def category_for(service: str) -> str:
    normalized = service.casefold()
    for category, fragments in _RULES.items():
        if any(fragment in normalized for fragment in fragments):
            return category
    return "Other"
