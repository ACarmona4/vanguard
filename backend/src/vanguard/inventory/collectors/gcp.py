from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..models import CollectionResult, Resource

DISABLED_SERVICE_REASONS = {"SERVICE_DISABLED", "accessNotConfigured"}
# BigQuery metadata and job history change slowly, and INFORMATION_SCHEMA queries are billed:
# refresh once per hour instead of on every inventory cycle.
BIGQUERY_REFRESH_SECONDS = 3600
_BIGQUERY_CACHE: dict[str, tuple[float, list]] = {}
TABLE_DETAILS_LIMIT = 500


def _has_disabled_service_reason(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("reason") in DISABLED_SERVICE_REASONS:
            return True
        return any(_has_disabled_service_reason(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_disabled_service_reason(item) for item in value)
    return False


def _service_is_disabled(error: Exception) -> bool:
    response = getattr(error, "response", None)
    if response is not None:
        try:
            if _has_disabled_service_reason(response.json()):
                return True
        except (AttributeError, ValueError):
            pass
    return _has_disabled_service_reason(getattr(error, "errors", None))


def _message_to_dict(message: Any) -> dict[str, Any]:
    from google.protobuf.json_format import MessageToDict

    protobuf = getattr(message, "_pb", message)
    return MessageToDict(protobuf, preserving_proto_field_name=True)


def _last_path_part(value: str | None) -> str | None:
    return value.rsplit("/", 1)[-1] if value else None


def _number(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _epoch_ms(value: Any) -> str | None:
    """BigQuery returns timestamps as milliseconds since the epoch, as strings."""
    from datetime import datetime, timezone

    number = _number(value)
    return datetime.fromtimestamp(number / 1000, timezone.utc).isoformat() if number else None


def _seconds(value: Any) -> int | None:
    """Durations arrive as timedelta (client libraries) or "600s" (REST)."""
    if value in (None, ""):
        return None
    if hasattr(value, "total_seconds"):
        return int(value.total_seconds()) or None
    try:
        return int(float(str(value).rstrip("s")))
    except ValueError:
        return None


class GCPCollector:
    def __init__(self, project_id: str, credentials=None):
        if credentials is None:
            raise ValueError("Saved GCP credentials are required")
        self.project_id = project_id
        self.credentials = credentials

    def collect(self) -> CollectionResult:
        result = CollectionResult()
        collectors: list[tuple[str, Callable[[], list[Resource]]]] = [
            ("compute.instances", self.instances),
            ("compute.disks", self.disks),
            ("compute.addresses", self.addresses),
            ("cloudsql.instances", self.sql_instances),
            ("storage.buckets", self.storage_buckets),
            ("run.services", self.run_services),
            ("cloudfunctions.functions", self.cloud_functions),
            ("bigquery.datasets", self.bigquery),
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
                if not _service_is_disabled(exc):
                    result.errors.append(f"{name}: {type(exc).__name__}; check permissions and configuration")
        return result

    def instances(self) -> list[Resource]:
        from google.cloud import compute_v1

        resources: list[Resource] = []
        client = compute_v1.InstancesClient(credentials=self.credentials)
        for scope, response in client.aggregated_list(project=self.project_id, timeout=20, retry=None):
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
                    region=(_last_path_part(instance.zone) or _last_path_part(scope)).rsplit("-", 1)[0],
                    status=instance.status,
                    attributes={
                        "machine_type": _last_path_part(instance.machine_type),
                        "private_ip": interface.network_i_p if interface else None,
                        "public_ip": public_ip,
                    },
                    raw_data=_message_to_dict(instance),
                ))
        return resources

    def disks(self) -> list[Resource]:
        from google.cloud import compute_v1

        resources: list[Resource] = []
        for scope, response in compute_v1.DisksClient(credentials=self.credentials).aggregated_list(
            project=self.project_id, timeout=20, retry=None
        ):
            for disk in response.disks or []:
                zone = _last_path_part(disk.zone) or _last_path_part(scope)
                resources.append(Resource(
                    provider="gcp", resource_type="persistent_disk", resource_id=str(disk.id),
                    name=disk.name, scope_id=self.project_id, zone=zone,
                    region=_last_path_part(disk.region) or (zone.rsplit("-", 1)[0] if zone else None),
                    status=disk.status,
                    attributes={
                        "size_gb": disk.size_gb,
                        "disk_type": _last_path_part(disk.type_),
                        "users": [_last_path_part(user) for user in disk.users],
                        "created_at": disk.creation_timestamp or None,
                        "last_detached_at": disk.last_detach_timestamp or None,
                        "labels": dict(disk.labels),
                    },
                    raw_data=_message_to_dict(disk),
                ))
        return resources

    def addresses(self) -> list[Resource]:
        from google.cloud import compute_v1

        resources: list[Resource] = []
        for scope, response in compute_v1.AddressesClient(credentials=self.credentials).aggregated_list(
            project=self.project_id, timeout=20, retry=None
        ):
            for address in response.addresses or []:
                resources.append(self._address(address, _last_path_part(address.region) or _last_path_part(scope)))
        for address in compute_v1.GlobalAddressesClient(credentials=self.credentials).list(
            project=self.project_id, timeout=20, retry=None
        ):
            resources.append(self._address(address, "global"))
        return resources

    def _address(self, address, region) -> Resource:
        return Resource(
            provider="gcp", resource_type="ip_address", resource_id=str(address.id),
            name=address.name, scope_id=self.project_id, region=region, status=address.status,
            attributes={
                "address": address.address,
                "address_type": address.address_type,
                "users": [_last_path_part(user) for user in address.users],
                "created_at": address.creation_timestamp or None,
            },
            raw_data=_message_to_dict(address),
        )

    def sql_instances(self) -> list[Resource]:
        from urllib.parse import quote

        from google.auth.transport.requests import AuthorizedSession
        resources = []
        with AuthorizedSession(self.credentials) as session:
            params = {}
            while True:
                response = session.get(
                    f"https://sqladmin.googleapis.com/sql/v1beta4/projects/{quote(self.project_id, safe='')}/instances",
                    params=params, timeout=20,
                )
                response.raise_for_status()
                body = response.json()
                for instance in body.get("items", []):
                    settings = instance.get("settings") or {}
                    resources.append(Resource(
                        provider="gcp", resource_type="cloudsql_instance",
                        resource_id=instance["name"], name=instance["name"],
                        scope_id=self.project_id, region=instance.get("region"),
                        status=instance.get("state"),
                        attributes={
                            "database_version": instance.get("databaseVersion"),
                            "tier": settings.get("tier"),
                            "availability_type": settings.get("availabilityType"),
                            "disk_size_gb": settings.get("dataDiskSizeGb"),
                            "backups_enabled": (settings.get("backupConfiguration") or {}).get("enabled"),
                            "labels": settings.get("userLabels") or {},
                        },
                        raw_data=instance,
                    ))
                if not body.get("nextPageToken"):
                    break
                params["pageToken"] = body["nextPageToken"]
        return resources

    def _list_all(self, url: str, key: str) -> list[dict[str, Any]]:
        from google.auth.transport.requests import AuthorizedSession

        items, params = [], {}
        with AuthorizedSession(self.credentials) as session:
            while True:
                response = session.get(url, params=params, timeout=20)
                response.raise_for_status()
                body = response.json()
                items.extend(body.get(key, []))
                if not body.get("nextPageToken"):
                    return items
                params["pageToken"] = body["nextPageToken"]

    def run_services(self) -> list[Resource]:
        resources = []
        for service in self._list_all(
            f"https://run.googleapis.com/v2/projects/{self.project_id}/locations/-/services", "services"
        ):
            region = service["name"].split("/locations/", 1)[1].split("/", 1)[0]
            name = _last_path_part(service["name"])
            template = service.get("template") or {}
            container = (template.get("containers") or [{}])[0]
            limits = (container.get("resources") or {}).get("limits") or {}
            scaling = {**(template.get("scaling") or {}), **(service.get("scaling") or {})}
            ready = (service.get("terminalCondition") or {}).get("state")
            resources.append(Resource(
                # Names are unique per region; billing and metrics identify services by name.
                provider="gcp", resource_type="cloud_run_service", resource_id=f"{region}/{name}",
                name=name, scope_id=self.project_id, region=region,
                status="READY" if ready == "CONDITION_SUCCEEDED" else ready,
                attributes={
                    "cpu": limits.get("cpu"),
                    "memory": limits.get("memory"),
                    # cpuIdle=false keeps CPU allocated, and billed, even without requests.
                    "cpu_always_allocated": (container.get("resources") or {}).get("cpuIdle") is False,
                    "min_instances": int(scaling.get("minInstanceCount") or 0),
                    "max_instances": scaling.get("maxInstanceCount"),
                    "concurrency": template.get("maxInstanceRequestConcurrency"),
                    "ingress": service.get("ingress"),
                    "created_at": service.get("createTime"),
                    "labels": service.get("labels") or {},
                },
                raw_data=service,
            ))
        return resources

    def cloud_functions(self) -> list[Resource]:
        resources = []
        for function in self._list_all(
            f"https://cloudfunctions.googleapis.com/v2/projects/{self.project_id}/locations/-/functions", "functions"
        ):
            region = function["name"].split("/locations/", 1)[1].split("/", 1)[0]
            name = _last_path_part(function["name"])
            config = function.get("serviceConfig") or {}
            resources.append(Resource(
                provider="gcp", resource_type="cloud_function", resource_id=f"{region}/{name}",
                name=name, scope_id=self.project_id, region=region, status=function.get("state"),
                attributes={
                    "generation": function.get("environment"),
                    "runtime": (function.get("buildConfig") or {}).get("runtime"),
                    "memory": config.get("availableMemory"),
                    "cpu": config.get("availableCpu"),
                    "timeout_seconds": config.get("timeoutSeconds"),
                    "min_instances": int(config.get("minInstanceCount") or 0),
                    "max_instances": config.get("maxInstanceCount"),
                    "run_service": _last_path_part(config.get("service")),
                    "updated_at": function.get("updateTime"),
                    "labels": function.get("labels") or {},
                },
                raw_data=function,
            ))
        return resources

    def bigquery(self) -> list[Resource]:
        import time

        cached = _BIGQUERY_CACHE.get(self.project_id)
        if cached and time.monotonic() - cached[0] < BIGQUERY_REFRESH_SECONDS:
            return cached[1]
        resources = self._bigquery_inventory()
        _BIGQUERY_CACHE[self.project_id] = (time.monotonic(), resources)
        return resources

    def _bigquery_inventory(self) -> list[Resource]:
        from urllib.parse import quote

        from google.auth.transport.requests import AuthorizedSession

        base = f"https://bigquery.googleapis.com/bigquery/v2/projects/{quote(self.project_id, safe='')}"
        resources, tables = [], []
        with AuthorizedSession(self.credentials) as session:
            def get(url):
                response = session.get(url, timeout=20)
                response.raise_for_status()
                return response.json()

            datasets = self._list_all(f"{base}/datasets", "datasets")
            for listed in datasets:
                dataset_id = listed["datasetReference"]["datasetId"]
                dataset = get(f"{base}/datasets/{quote(dataset_id, safe='')}")
                resources.append(Resource(
                    # The billing export names datasets by ID.
                    provider="gcp", resource_type="bigquery_dataset", resource_id=dataset_id,
                    name=dataset_id, scope_id=self.project_id, region=(dataset.get("location") or "").lower() or None,
                    attributes={
                        "default_table_expiration_ms": _number(dataset.get("defaultTableExpirationMs")),
                        "default_partition_expiration_ms": _number(dataset.get("defaultPartitionExpirationMs")),
                        "storage_billing_model": dataset.get("storageBillingModel") or "LOGICAL",
                        "created_at": _epoch_ms(dataset.get("creationTime")),
                        "labels": dataset.get("labels") or {},
                    },
                    raw_data=dataset,
                ))
                for table in self._list_all(f"{base}/datasets/{quote(dataset_id, safe='')}/tables", "tables"):
                    tables.append((dataset, table))
            for index, (dataset, table) in enumerate(tables):
                dataset_id, table_id = dataset["datasetReference"]["datasetId"], table["tableReference"]["tableId"]
                # Size needs one call per table; very large projects keep the listing fields only.
                details = table if index >= TABLE_DETAILS_LIMIT else get(
                    f"{base}/datasets/{quote(dataset_id, safe='')}/tables/{quote(table_id, safe='')}")
                partitioning = details.get("timePartitioning") or details.get("rangePartitioning")
                resources.append(Resource(
                    provider="gcp", resource_type="bigquery_table", resource_id=f"{dataset_id}.{table_id}",
                    name=f"{dataset_id}.{table_id}", scope_id=self.project_id,
                    region=(dataset.get("location") or "").lower() or None,
                    attributes={
                        "dataset": dataset_id,
                        "table_type": details.get("type"),
                        "partitioned": bool(partitioning),
                        "partition_field": (details.get("timePartitioning") or {}).get("field")
                        or (details.get("rangePartitioning") or {}).get("field"),
                        "clustered": bool(details.get("clustering")),
                        "require_partition_filter": bool(details.get("requirePartitionFilter")
                                                         or (details.get("timePartitioning") or {}).get("requirePartitionFilter")),
                        "expires_at": _epoch_ms(details.get("expirationTime")),
                        "size_bytes": _number(details.get("numBytes")),
                        "long_term_bytes": _number(details.get("numLongTermBytes")),
                        "rows": _number(details.get("numRows")),
                        "created_at": _epoch_ms(details.get("creationTime")),
                        "labels": details.get("labels") or {},
                    },
                    raw_data={key: value for key, value in details.items() if key != "schema"},
                ))
            usage = self._bigquery_usage(session, {(dataset.get("location") or "US") for dataset, _ in tables})
        for resource in resources:
            if resource.resource_type == "bigquery_table":
                # None means the job history could not be read, not that the table is unused.
                queries, billed = (None, None) if usage is None else usage.get(resource.resource_id, (0, 0))
                resource.attributes["queries_7d"] = queries
                resource.attributes["bytes_billed_7d"] = billed
        return resources

    def _bigquery_usage(self, session, locations) -> dict | None:
        """Queries and bytes billed in the last 7 days by queries that read each table.

        A query that joins two tables counts its bytes for both. Returns None when the
        job history is not readable (bigquery.jobs.listAll), so tables show "unknown".
        """
        from ...costs.collectors import _bigquery_rows

        parameters = [{"name": "project_id", "parameterType": {"type": "STRING"},
                       "parameterValue": {"value": self.project_id}}]
        usage: dict = {}
        try:
            for location in sorted(locations):
                query = f"""
                  SELECT CONCAT(ref.dataset_id, '.', ref.table_id) AS table_name,
                         COUNT(DISTINCT job_id) AS queries, SUM(total_bytes_billed) AS bytes_billed
                  FROM `region-{location.lower()}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT, UNNEST(referenced_tables) AS ref
                  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
                    AND job_type = 'QUERY' AND state = 'DONE' AND ref.project_id = @project_id
                  GROUP BY table_name
                """
                for row in _bigquery_rows(session, self.project_id, location, query, parameters):
                    usage[row["table_name"]] = (int(row["queries"] or 0), int(row["bytes_billed"] or 0))
        except Exception:
            return None
        return usage

    def storage_buckets(self) -> list[Resource]:
        from google.auth.transport.requests import AuthorizedSession

        resources = []
        with AuthorizedSession(self.credentials) as session:
            params = {"project": self.project_id}
            while True:
                response = session.get("https://storage.googleapis.com/storage/v1/b", params=params, timeout=20)
                response.raise_for_status()
                body = response.json()
                for bucket in body.get("items", []):
                    rules = (bucket.get("lifecycle") or {}).get("rule") or []
                    resources.append(Resource(
                        # The billing export names buckets by name, so the name is the identifier.
                        provider="gcp", resource_type="storage_bucket", resource_id=bucket["name"],
                        name=bucket["name"], scope_id=self.project_id,
                        region=(bucket.get("location") or "").lower() or None,
                        attributes={
                            "location_type": bucket.get("locationType"),
                            "storage_class": bucket.get("storageClass"),
                            "autoclass": bool((bucket.get("autoclass") or {}).get("enabled")),
                            "lifecycle_rules": len(rules),
                            "lifecycle_actions": sorted({(rule.get("action") or {}).get("type") for rule in rules} - {None}),
                            "soft_delete_seconds": _seconds((bucket.get("softDeletePolicy") or {}).get("retentionDurationSeconds")),
                            "versioning": bool((bucket.get("versioning") or {}).get("enabled")),
                            "created_at": bucket.get("timeCreated"),
                            "labels": bucket.get("labels") or {},
                        },
                        raw_data=bucket,
                    ))
                if not body.get("nextPageToken"):
                    break
                params["pageToken"] = body["nextPageToken"]
        return resources

    def networks(self) -> list[Resource]:
        from google.cloud import compute_v1

        return [Resource(
            provider="gcp", resource_type="vpc_network", resource_id=str(network.id),
            name=network.name, scope_id=self.project_id, raw_data=_message_to_dict(network),
        ) for network in compute_v1.NetworksClient(credentials=self.credentials).list(
            project=self.project_id, timeout=20, retry=None
        )]

    def subnetworks(self) -> list[Resource]:
        from google.cloud import compute_v1

        resources: list[Resource] = []
        for scope, response in compute_v1.SubnetworksClient(
            credentials=self.credentials
        ).aggregated_list(project=self.project_id, timeout=20, retry=None):
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
        ) for rule in compute_v1.FirewallsClient(credentials=self.credentials).list(
            project=self.project_id, timeout=20, retry=None
        )]

    def pubsub_topics(self) -> list[Resource]:
        from google.cloud import pubsub_v1

        parent = f"projects/{self.project_id}"
        client = pubsub_v1.PublisherClient(credentials=self.credentials)
        resources = []
        for topic in client.list_topics(request={"project": parent}, timeout=20, retry=None):
            try:
                subscriptions = len(list(client.list_topic_subscriptions(
                    request={"topic": topic.name}, timeout=20, retry=None)))
            except Exception:  # missing pubsub.topics.listSubscriptions keeps the topic
                subscriptions = None
            resources.append(Resource(
                provider="gcp", resource_type="pubsub_topic", resource_id=topic.name,
                name=_last_path_part(topic.name), scope_id=self.project_id,
                attributes={
                    "subscriptions": subscriptions,
                    "retention_seconds": _seconds(topic.message_retention_duration),
                    "labels": dict(topic.labels),
                },
                raw_data=_message_to_dict(topic),
            ))
        return resources

    def pubsub_subscriptions(self) -> list[Resource]:
        from google.cloud import pubsub_v1

        parent = f"projects/{self.project_id}"
        return [Resource(
            provider="gcp", resource_type="pubsub_subscription", resource_id=subscription.name,
            name=_last_path_part(subscription.name), scope_id=self.project_id,
            status=str(subscription.state.name),
            attributes={
                "topic": subscription.topic, "filter": subscription.filter,
                "retention_seconds": _seconds(subscription.message_retention_duration),
                "retain_acked_messages": subscription.retain_acked_messages,
                "push": bool(subscription.push_config.push_endpoint),
                "expiration_ttl_seconds": _seconds(subscription.expiration_policy.ttl),
                "labels": dict(subscription.labels),
            },
            raw_data=_message_to_dict(subscription),
        ) for subscription in pubsub_v1.SubscriberClient(
            credentials=self.credentials
        ).list_subscriptions(
            request={"project": parent}, timeout=20, retry=None
        )]

    def service_accounts(self) -> list[Resource]:
        from google.cloud import iam_admin_v1

        return [Resource(
            provider="gcp", resource_type="service_account", resource_id=account.unique_id,
            name=account.email, scope_id=self.project_id, raw_data=_message_to_dict(account),
        ) for account in iam_admin_v1.IAMClient(
            credentials=self.credentials
        ).list_service_accounts(
            request={"name": f"projects/{self.project_id}"}, timeout=20, retry=None
        )]

    def project_policy(self) -> list[Resource]:
        from google.cloud import resourcemanager_v3
        from google.iam.v1 import iam_policy_pb2

        policy = resourcemanager_v3.ProjectsClient(
            credentials=self.credentials
        ).get_iam_policy(
            request=iam_policy_pb2.GetIamPolicyRequest(resource=f"projects/{self.project_id}"), timeout=20, retry=None
        )
        return [Resource(
            provider="gcp", resource_type="project_iam_policy",
            resource_id=f"projects/{self.project_id}/iamPolicy", name="iamPolicy",
            scope_id=self.project_id, raw_data=_message_to_dict(policy),
        )]
