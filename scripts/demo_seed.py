"""Load the demo scenarios into a separate demo workspace so they can be seen in the app.

It creates its own user and organization (real data is never touched), saves the
scenario resources to the inventory, writes invented resource-level billing lines,
and runs the real engine, economic assessment and persistence, as the background
analysis would. The demo connections are disabled, so no worker tries to reach a cloud.

Usage, from the repository root (the app's PostgreSQL must be running):
    PYTHONPATH=backend/src .venv/bin/python scripts/demo_seed.py           # create or recreate
    PYTHONPATH=backend/src .venv/bin/python scripts/demo_seed.py --reset   # delete the demo workspace

The login is written to .vanguard/demo-credentials.txt (ignored by git).
The background analysis re-evaluates every workspace from Prometheus every few minutes,
and the demo resources have no real metrics there. To keep the demo intact while presenting,
start the app with VANGUARD_RECOMMENDATIONS_ENABLED=false, or run this script again.
"""

import argparse
import importlib.util
import os
import secrets
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from vanguard.accounts import auth
from vanguard.connections import repository as connections
from vanguard.inventory import database
from vanguard.inventory.models import Resource
from vanguard.recommendations import economics
from vanguard.recommendations import repository as recommendations
from vanguard.recommendations.engine import confirmed_checks, evaluate
from vanguard.recommendations.policy import DEFAULT_POLICY, SOURCES

ROOT = Path(__file__).resolve().parents[1]
DEMO_EMAIL = "demo@vanguard.test"
CREDENTIALS = ROOT / ".vanguard" / "demo-credentials.txt"
CONNECTIONS = {
    "demo-gcp": ("gcp", "Demo GCP", "demo-gcp", True),
    "demo-aws": ("aws", "Demo AWS", "demo-aws", True),
    "demo-aws-academy": ("aws", "Demo AWS Academy", "demo-aws-academy", False),
}
SERVICES = {"compute_instance": "Compute Engine", "persistent_disk": "Compute Engine",
            "ip_address": "Compute Engine", "ec2_instance": "Amazon Elastic Compute Cloud - Compute",
            "ebs_volume": "EC2 - Other"}
INVENTORY_FIELDS = ("provider", "resource_type", "resource_id", "name", "region", "status", "attributes", "raw_data")


def load_demo():
    path = ROOT / "scripts" / "demo_recomendaciones.py"
    spec = importlib.util.spec_from_file_location("demo_recomendaciones", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reset(connection):
    user = connection.execute("SELECT id FROM app_users WHERE email = %s", (DEMO_EMAIL,)).fetchone()
    if user:
        # Organization data cascades from the workspace; the user row goes last.
        connection.execute("DELETE FROM organizations WHERE id = %s", (user["id"],))
        connection.execute("DELETE FROM app_users WHERE id = %s", (user["id"],))
    connection.commit()
    return bool(user)


def shifted(series, owner_id, scope, offset):
    """Demo series use a fixed clock; move them to now and into the demo workspace."""
    return [{"metric": {**item["metric"], "owner_id": owner_id, "scope_id": scope(item["metric"])},
             "values": [[stamp + offset, value] for stamp, value in item["values"]]} for item in series]


def seed(connection, database_url):
    demo = load_demo()
    password = secrets.token_urlsafe(12)
    user = auth.create_user(connection, email=DEMO_EMAIL, full_name="Vanguard Demo", password=password)
    owner_id = str(user["id"])
    created = {}
    for key, (provider, name, scope, detail) in CONNECTIONS.items():
        row = connections.create_connection(
            connection, owner_id=owner_id, provider=provider, name=name, scope_id=scope,
            regions=["us-east-1" if provider == "aws" else "us-central1"],
            credential_hint="demo", identity="demo", encrypted_credentials="demo-not-a-credential",
        )
        connection.execute("UPDATE cloud_connections SET enabled = FALSE, status = 'success' WHERE id = %s", (row["id"],))
        connection.execute(
            """INSERT INTO cost_sources (connection_id, owner_id, provider, enabled, status, detail_available, last_synced_at)
            VALUES (%s, %s, %s, FALSE, 'success', %s, NOW())""",
            (row["id"], owner_id, provider, detail),
        )
        created[key] = str(row["id"])
    connection.commit()

    scenarios = [item for item in demo.scenarios() if item.get("telemetry_available", True)]
    by_connection = defaultdict(dict)
    for scenario in scenarios:
        for item in (scenario["item"], *scenario.get("others", [])):
            by_connection[item["connection_id"]][item["resource_id"]] = item
    for key, items in by_connection.items():
        database.save_resources(database_url, [
            Resource(scope_id=CONNECTIONS[key][2], **{field: item.get(field) or ({} if field in {"attributes", "raw_data"} else None)
                                                      for field in INVENTORY_FIELDS})
            for item in items.values()
        ], owner_id=owner_id, connection_id=created[key])

    rows = connection.execute(
        "SELECT id, owner_id, connection_id, provider, scope_id, region, resource_type, resource_id, name, status, "
        "attributes, raw_data, last_seen_at FROM inventory_resources WHERE owner_id = %s ORDER BY id", (owner_id,),
    ).fetchall()
    inventory = {(row["provider"], row["resource_type"], row["resource_id"]): row for row in rows}
    scope_of = {created[key]: CONNECTIONS[key][2] for key in CONNECTIONS}

    now = time.time()
    offset = int(now) // 300 * 300 - demo.NOW
    day_offset = date.today() - demo.TODAY
    findings, checked, all_recent, all_history = [], set(), [], []
    for scenario in scenarios:
        members = [inventory[(item["provider"], item["resource_type"], item["resource_id"])]
                   for item in (scenario["item"], *scenario.get("others", []))]
        main = members[0]

        def scope(labels, main=main):
            return main["scope_id"]

        recent = shifted(scenario["recent"], owner_id, scope, offset)
        history = shifted(scenario["history"], owner_id, scope, offset)
        options = {"now": now, "telemetry_available": True, "history_available": scenario.get("history_available", True)}
        found, _ = evaluate(members, recent, history, **options)
        findings += [item for item in found if item["resource_id"] == main["id"]]
        checked |= set(confirmed_checks(members, recent, history, **options))
        all_recent += recent
        all_history += history
        for line in scenario.get("billing", []):
            billed_id = line["resource_id"]
            billed_type = economics.billing_key(line["provider"], billed_id)[0]
            connection.execute(
                """INSERT INTO cost_records (owner_id, connection_id, usage_date, provider, scope_id, level, service,
                category, resource_id, resource_name, region, currency, amount)
                VALUES (%s, %s, %s, %s, %s, 'resource', %s, %s, %s, %s, %s, %s, %s)""",
                (owner_id, created[line["connection_id"]], line["usage_date"] + day_offset, line["provider"],
                 scope_of[created[line["connection_id"]]], SERVICES[billed_type],
                 "Storage" if billed_type == "ebs_volume" else "Compute", billed_id, main["name"], main["region"],
                 line["currency"], line["amount"]),
            )
    # Service totals, so the Costs page shows the same invented spend.
    connection.execute(
        """INSERT INTO cost_records (owner_id, connection_id, usage_date, provider, scope_id, level, service, category,
        region, currency, amount)
        SELECT owner_id, connection_id, usage_date, provider, scope_id, 'service', service, category,
               max(region), currency, sum(amount)
        FROM cost_records WHERE owner_id = %s AND level = 'resource'
        GROUP BY owner_id, connection_id, usage_date, provider, scope_id, service, category, currency""",
        (owner_id,),
    )
    connection.commit()

    economics.assess(connection, owner_id, findings, rows)
    _, summary = evaluate(rows, all_recent, all_history, now=now)
    recommendations.save_evaluation(connection, owner_id, {
        "generated_at": datetime.fromtimestamp(now, timezone.utc),
        "items": findings,
        "checked_keys": sorted(checked),
        "summary": summary,
        "telemetry_status": "available",
        "notices": ["Demo workspace: resources, metrics and billing are invented to show every recommendation."],
        "collection": {"status": "success", "last_finished_at": None, "errors": []},
        "policy": asdict(DEFAULT_POLICY),
        "sources": SOURCES,
    })
    connection.commit()

    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS.write_text(f"Vanguard demo workspace\nemail: {DEMO_EMAIL}\npassword: {password}\n")
    CREDENTIALS.chmod(0o600)
    return len(rows), len(findings), sum(1 for item in findings if (item.get("cost") or {}).get("savings"))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="delete the demo workspace and exit")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        sys.exit("DATABASE_URL is not configured (.env).")
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        existed = reset(connection)
        if args.reset:
            print("Demo workspace deleted." if existed else "There was no demo workspace.")
            return
        resources, findings, savings = seed(connection, database_url)
    print(f"Demo workspace ready: {resources} resources, {findings} recommendations, {savings} with estimated savings.")
    print(f"Login details: {CREDENTIALS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
