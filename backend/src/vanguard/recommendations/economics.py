"""Economic assessment kept apart from technical rules: observed billing only, never list prices."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from ..costs.exchange import MAX_CARRY_DAYS
from ..costs.identity import billing_key, resource_billing_keys
from .policy import DEFAULT_POLICY

# Removing the whole resource avoids its cost; the savings equal what it is billed today.
SAVINGS_RULES = {"unattached_volume", "unused_address", "stopped_instance_storage"}
# Resizing changes the price; without the alternative's price only the current cost is shown.
COST_ONLY_RULES = {
    "cpu_percent_oversized", "filesystem_percent_oversized", "dynamodb_read_capacity", "dynamodb_write_capacity",
    "serverless_idle_capacity", "sqs_short_polling", "bucket_lifecycle", "snapshot_retention",
    "bigquery_partitioning", "bigquery_retention", "bigquery_partition_filter",
}

def _load(connection, owner_id, start, end):
    records = connection.execute(
        f"""SELECT r.connection_id, r.provider, r.scope_id, r.region, r.resource_id, r.usage_date,
                   r.currency, r.amount, r.estimated, fx.cop_per_usd, fx.valid_to AS exchange_valid_to
        FROM cost_records r LEFT JOIN LATERAL (
            SELECT cop_per_usd, valid_to FROM cost_exchange_rates
            WHERE valid_from <= r.usage_date AND valid_to >= r.usage_date - {MAX_CARRY_DAYS}
            ORDER BY valid_from DESC LIMIT 1
        ) fx ON r.currency = 'COP'
        WHERE r.owner_id = %s AND r.level = 'resource' AND r.usage_date >= %s AND r.usage_date < %s""",
        (owner_id, start, end),
    ).fetchall()
    sources = {
        str(row["connection_id"]): row
        for row in connection.execute(
            "SELECT connection_id, provider, status, detail_available FROM cost_sources WHERE owner_id = %s",
            (owner_id,),
        ).fetchall()
    }
    lines = defaultdict(list)
    for original in records:
        record = dict(original)
        if record['currency'] == 'COP' and record.get('cop_per_usd'):
            record['amount'] = Decimal(record['amount']) / Decimal(record['cop_per_usd'])
            record['currency'] = 'USD'
            record['fx_carried'] = record.get('exchange_valid_to', record['usage_date']) < record['usage_date']
        key = billing_key(record["provider"], record["resource_id"])
        if key:
            lines[(str(record["connection_id"]), *key)].append(record)
    return lines, sources


def _unavailable(provider, source, reason=None):
    if reason is None:
        if source is None or source["status"] in {"unconfigured", "ready"}:
            reason = "No billing source is configured for this connection."
        elif provider == "aws" and not source["detail_available"]:
            reason = ("AWS Cost Explorer resource-level data is not available for this account "
                      "(enable daily resource detail for each service; the API covers the last 14 days).")
        else:
            reason = "No billing lines for this resource in the period (the export can lag 24 to 48 hours)."
    return {"status": "not_attributable", "reason": reason, "savings": None}


def _confidence(days, policy):
    if days >= policy.cost_high_confidence_days:
        return "high"
    return "medium" if days >= policy.cost_medium_confidence_days else "low"


def suggested_impact(cost, policy=DEFAULT_POLICY):
    return None


def assess(connection, owner_id, findings, resources, *, today=None, policy=DEFAULT_POLICY):
    """Attach an observed-cost block to cost findings. Mutates and returns `findings`."""
    targets = [item for item in findings if item["rule"] in SAVINGS_RULES | COST_ONLY_RULES]
    if not targets:
        return findings
    end = today or date.today()  # The current partial billing day is excluded.
    start = end - timedelta(days=policy.cost_lookback_days)
    lines, sources = _load(connection, owner_id, start, end)
    inventory = {item["id"]: item for item in resources}
    volumes = {(str(item.get("connection_id")), item["resource_id"]): item
               for item in resources if item["resource_type"] == "ebs_volume"}
    for finding in targets:
        resource = inventory.get(finding["resource_id"])
        if resource is None:
            continue
        connection_id = str(resource.get("connection_id"))
        if finding["rule"] == "stopped_instance_storage":
            mappings = (resource.get("raw_data") or {}).get("BlockDeviceMappings") or []
            billed = [(connection_id, "ebs_volume", item["Ebs"]["VolumeId"]) for item in mappings
                      if (item.get("Ebs") or {}).get("VolumeId") and (connection_id, item["Ebs"]["VolumeId"]) in volumes]
        else:
            candidates = [(connection_id, *key) for key in resource_billing_keys(resource)]
            billed = [key for key in candidates if key in lines] or candidates
        if any(key not in lines for key in billed):
            finding["cost"] = _unavailable(resource["provider"], sources.get(connection_id))
            continue
        records = [record for key in billed for record in lines[key]
                   if (not record.get("scope_id") or record["scope_id"] == resource.get("scope_id"))
                   and (not record.get("region") or not resource.get("region") or record["region"] == resource["region"])]
        if not records:
            finding["cost"] = _unavailable(resource["provider"], sources.get(connection_id))
            continue
        currencies = {record["currency"] for record in records}
        if currencies != {"USD"}:
            finding["cost"] = _unavailable(resource["provider"], None, "USD estimate unavailable: missing historical exchange rate or unsupported billing currency.")
            continue
        days = len({record["usage_date"] for record in records})
        observed = sum((Decimal(record["amount"]) for record in records), Decimal(0))
        daily = observed / days
        monthly = float(round(daily * 30, 2))
        cost = {
            "status": "attributed",
            "source": "gcp_billing_export" if resource["provider"] == "gcp" else "aws_cost_explorer",
            "granularity": "resource",
            "currency": "USD",
            "billing_components": ["|".join(key) for key in billed],
            "period_start": min(record["usage_date"] for record in records).isoformat(),
            "period_end": max(record["usage_date"] for record in records).isoformat(),
            "days_covered": days,
            "observed_amount": float(round(observed, 2)),
            "daily_average": float(round(daily, 4)),
            "monthly_cost": monthly,
            "savings": None,
            "reason": None,
        }
        finding["cost"] = cost
    return findings
