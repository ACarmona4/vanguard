"""Economic assessment kept apart from technical rules: observed billing only, never list prices."""

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from .policy import DEFAULT_POLICY

# Removing the whole resource avoids its cost; the savings equal what it is billed today.
SAVINGS_RULES = {"unattached_volume", "unused_address", "stopped_instance_storage"}
# Resizing changes the price; without the alternative's price only the current cost is shown.
COST_ONLY_RULES = {"cpu_percent_oversized", "filesystem_percent_oversized"}

# //compute.googleapis.com/projects/<number>/zones/<zone>/instances/<id>
_GCP_KINDS = {"instances": "compute_instance", "disk": "persistent_disk", "disks": "persistent_disk",
              "addresses": "ip_address", "globalAddresses": "ip_address"}
_GCP_NAME = re.compile(r"^//compute\.googleapis\.com/projects/[^/]+/(?:zones|regions|global)(?:/[^/]+)?/"
                       r"(?P<kind>[A-Za-z]+)/(?P<id>\d+)$")
_GCS_NAME = re.compile(r"^//storage\.googleapis\.com/projects/[^/]+/buckets/(?P<name>[a-z0-9][a-z0-9._-]*)$")
_AWS_PREFIXES = {"i-": "ec2_instance", "vol-": "ebs_volume"}


def billing_key(provider, billed_id):
    """(resource_type, provider resource id) for a billing line, or None when it cannot be matched."""
    if not billed_id:
        return None
    if provider == "gcp":
        bucket = _GCS_NAME.match(billed_id)
        if bucket:
            return "storage_bucket", bucket["name"]
        match = _GCP_NAME.match(billed_id)
        kind = _GCP_KINDS.get(match["kind"]) if match else None
        return (kind, match["id"]) if kind else None
    if provider == "aws":
        for prefix, kind in _AWS_PREFIXES.items():
            if billed_id.startswith(prefix):
                return kind, billed_id
    return None


def _load(connection, owner_id, start, end):
    records = connection.execute(
        """SELECT connection_id, provider, resource_id, usage_date, currency, amount FROM cost_records
        WHERE owner_id = %s AND level = 'resource' AND usage_date >= %s AND usage_date < %s""",
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
    for record in records:
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
                      "(it must be enabled and only covers EC2 for the last 14 days).")
        else:
            reason = "No billing lines for this resource in the period (the export can lag 24 to 48 hours)."
    return {"status": "not_attributable", "reason": reason, "savings": None}


def _confidence(days, policy):
    if days >= policy.cost_high_confidence_days:
        return "high"
    return "medium" if days >= policy.cost_medium_confidence_days else "low"


def suggested_impact(cost, policy=DEFAULT_POLICY):
    savings = (cost or {}).get("savings")
    if not savings:
        return None
    amount = savings["monthly_amount"]
    if amount >= policy.savings_high:
        return "high"
    if amount >= policy.savings_medium:
        return "medium"
    return "low" if amount > 0 else None


def assess(connection, owner_id, findings, resources, *, today=None, policy=DEFAULT_POLICY):
    """Attach an observed-cost block to cost findings. Mutates and returns `findings`."""
    targets = [item for item in findings if item["rule"] in SAVINGS_RULES | COST_ONLY_RULES]
    if not targets:
        return findings
    end = (today or date.today()) + timedelta(days=1)
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
            subject = "the attached volumes"
        else:
            billed = [(connection_id, resource["resource_type"], resource["resource_id"])]
            subject = "this resource"
        if any(key not in lines for key in billed):
            finding["cost"] = _unavailable(resource["provider"], sources.get(connection_id))
            continue
        records = [record for key in billed for record in lines[key]]
        currencies = {record["currency"] for record in records}
        if len(currencies) != 1:
            finding["cost"] = _unavailable(resource["provider"], None, "Billing lines use more than one currency.")
            continue
        days = len({record["usage_date"] for record in records})
        observed = sum((Decimal(record["amount"]) for record in records), Decimal(0))
        daily = observed / days
        monthly = float(round(daily * 30, 2))
        cost = {
            "status": "attributed",
            "source": "gcp_billing_export" if resource["provider"] == "gcp" else "aws_cost_explorer",
            "granularity": "resource",
            "currency": currencies.pop(),
            "period_start": min(record["usage_date"] for record in records).isoformat(),
            "period_end": max(record["usage_date"] for record in records).isoformat(),
            "days_covered": days,
            "observed_amount": float(round(observed, 2)),
            "daily_average": float(round(daily, 4)),
            "monthly_cost": monthly,
            "savings": None,
            "reason": None,
        }
        assumptions = ["Usage cost before credits and discounts, as billed.",
                       "Days without billing lines are not counted in the average."]
        if finding["rule"] in SAVINGS_RULES and monthly > 0:
            cost["savings"] = {
                "monthly_amount": monthly,
                "formula": f"Average daily cost of {subject} over {days} billed day(s) × 30 days",
                "confidence": _confidence(days, policy),
                "assumptions": assumptions + ["Assumes the resource is removed; snapshots or replacements are not priced."],
            }
        elif finding["rule"] in SAVINGS_RULES:
            cost["reason"] = "Savings not estimated: the resource had no billed cost in the period."
        elif finding["rule"] in COST_ONLY_RULES:
            cost["reason"] = "Savings not estimated: the price of a smaller size is not available."
        finding["cost"] = cost
    return findings
