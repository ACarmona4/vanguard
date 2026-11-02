"""Provider billing identifiers normalized without guessing ambiguous bare names."""

import re
from urllib.parse import urlparse

_COMPUTE = re.compile(r"^//compute\.googleapis\.com/projects/[^/]+/(?:zones|regions|global)(?:/[^/]+)?/(?P<kind>[A-Za-z]+)/(?P<id>\d+)$")
_KINDS = {"instances": "compute_instance", "disk": "persistent_disk", "disks": "persistent_disk",
          "addresses": "ip_address", "globalAddresses": "ip_address"}
_REGIONAL = re.compile(r"^//(?P<api>run|cloudfunctions)\.googleapis\.com/projects/[^/]+/locations/(?P<region>[^/]+)/(?:services|functions)/(?P<name>[^/]+)$")
_BIGQUERY = re.compile(r"^//bigquery\.googleapis\.com/projects/[^/]+/datasets/(?P<dataset>[A-Za-z0-9_]+)(?:/tables/(?P<table>[A-Za-z0-9_]+))?$")
_BUCKET = re.compile(r"^//storage\.googleapis\.com/projects/[^/]+/buckets/(?P<name>[a-z0-9][a-z0-9._-]*)$")
_SQL = re.compile(r"^//(?:sqladmin|cloudsql)\.googleapis\.com/projects/[^/]+/instances/(?P<name>[^/]+)$")
_PUBSUB = re.compile(r"^(?://pubsub\.googleapis\.com/)?(?P<name>projects/[^/]+/(?P<kind>topics|subscriptions)/[^/]+)$")


def billing_key(provider, billed_id):
    if not billed_id:
        return None
    if provider == "gcp":
        for pattern, kind, field in ((_BUCKET, "storage_bucket", "name"), (_SQL, "cloudsql_instance", "name")):
            match = pattern.fullmatch(billed_id)
            if match:
                return kind, match[field]
        match = _BIGQUERY.fullmatch(billed_id)
        if match:
            return ("bigquery_table", f"{match['dataset']}.{match['table']}") if match["table"] else ("bigquery_dataset", match["dataset"])
        match = _REGIONAL.fullmatch(billed_id)
        if match:
            return {"run": "cloud_run_service", "cloudfunctions": "cloud_function"}[match["api"]], f"{match['region']}/{match['name']}"
        match = _PUBSUB.fullmatch(billed_id)
        if match:
            return ("pubsub_topic" if match["kind"] == "topics" else "pubsub_subscription"), match["name"]
        match = _COMPUTE.fullmatch(billed_id)
        if match and match["kind"] in _KINDS:
            return _KINDS[match["kind"]], match["id"]
    if provider == "aws":
        for prefix, kind in (("i-", "ec2_instance"), ("vol-", "ebs_volume")):
            if re.fullmatch(prefix + r"[a-zA-Z0-9]+", billed_id):
                return kind, billed_id
        parts = billed_id.split(":", 5)
        if len(parts) != 6 or parts[0] != "arn" or not parts[1].startswith("aws"):
            return None
        service, identifier = parts[2], parts[5]
        kind = {"lambda": "lambda_function", "dynamodb": "dynamodb_table", "sns": "sns_topic", "sqs": "sqs_queue", "s3": "s3_bucket"}.get(service)
        if service == "rds":
            kind = {"db": "rds_instance", "cluster": "rds_cluster", "snapshot": "rds_snapshot", "cluster-snapshot": "rds_snapshot"}.get(identifier.split(":")[0])
        if service == "lambda" and identifier.startswith("function:"):
            billed_id = ":".join(parts[:5] + [":".join(identifier.split(":")[:2])])
        if service == "s3":
            return (kind, identifier) if identifier and "/" not in identifier else None
        if kind:
            return kind, billed_id
    return None


def resource_billing_key(resource):
    if resource["provider"] == "aws" and resource["resource_type"] == "sqs_queue":
        parsed = urlparse(resource["resource_id"])
        path = parsed.path.strip("/").split("/")
        if len(path) == 2 and parsed.hostname and parsed.hostname.startswith("sqs."):
            partition = "aws-cn" if parsed.hostname.endswith(".com.cn") else "aws-us-gov" if ".us-gov-" in parsed.hostname else "aws"
            return "sqs_queue", f"arn:{partition}:sqs:{parsed.hostname.split('.')[1]}:{path[0]}:{path[1]}"
    return billing_key(resource["provider"], resource["resource_id"]) or (resource["resource_type"], resource["resource_id"])


def resource_billing_keys(resource):
    keys = [resource_billing_key(resource)]
    attributes = resource.get("attributes") or {}
    if (resource["provider"] == "gcp" and resource["resource_type"] == "cloud_function"
            and attributes.get("generation") == "GEN_2" and attributes.get("run_service") and resource.get("region")):
        keys.append(("cloud_run_service", f"{resource['region']}/{attributes['run_service']}"))
    return keys
