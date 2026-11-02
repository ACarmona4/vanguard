"""Upgrade the existing AWS lab with persistent PaaS resources and seed synthetic traffic.

PYTHONPATH=backend/src .venv/bin/python scripts/paas_lab.py --deployment-id UUID --action deploy
The existing Delete lab action removes all managed resources, including PaaS and its schedule.
"""

import argparse
import json
import os
import time
from pathlib import Path

import boto3
import psycopg
from botocore.config import Config
from psycopg.rows import dict_row
from vanguard.connections.credentials import decrypt_credentials
from vanguard.core.config import load_environment
from vanguard.labs import service


def seed(session, outputs):
    """Invoke the producer, then verify real SNS/SQS/Lambda delivery in DynamoDB."""
    config = Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 1})
    client = session.client("lambda", config=config)
    mapping = session.client("lambda", config=config)
    for _ in range(30):
        sources = mapping.list_event_source_mappings(FunctionName=outputs["consumer"])["EventSourceMappings"]
        if sources and all(item["State"] == "Enabled" for item in sources):
            break
        time.sleep(2)
    else:
        raise RuntimeError("The SQS consumer mapping is not ready")
    messages = []
    for _ in range(3):
        response = client.invoke(FunctionName=outputs["producer"], Payload=b"{}")
        body = json.loads(response["Payload"].read())
        if response.get("FunctionError"):
            raise RuntimeError("Lab producer failed; inspect its CloudWatch log group")
        messages.append(body["id"])
    response = client.invoke(FunctionName=outputs["producer"], Payload=b'{"simulate_error":true}')
    response["Payload"].read()
    controlled_error = bool(response.get("FunctionError"))
    database = session.client("dynamodb", config=config)
    deadline = time.monotonic() + 90
    delivered = set()
    while time.monotonic() < deadline:
        for identifier in messages:
            if identifier in delivered:
                continue
            record = database.get_item(TableName=outputs["table"], Key={"id": {"S": "processed#"+identifier}}, ConsistentRead=True)
            if record.get("Item", {}).get("status", {}).get("S") == "processed":
                delivered.add(identifier)
        if len(delivered) == len(messages):
            return {"published": len(messages), "processed": len(delivered), "controlled_error": controlled_error}
        time.sleep(2)
    raise RuntimeError("Messages have not completed the PaaS flow; inspect the consumer and dead-letter queue")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--action", choices=("deploy", "seed", "status"), default="status")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    load_environment()
    database_url = os.environ["DATABASE_URL"]
    with psycopg.connect(database_url, row_factory=dict_row) as database:
        deployment = database.execute(
            "SELECT d.*, c.scope_id, c.encrypted_credentials FROM lab_deployments d "
            "JOIN cloud_connections c ON c.id=d.connection_id AND c.owner_id=d.owner_id "
            "WHERE d.id=%s AND d.provider='aws' AND c.enabled", (args.deployment_id,),
        ).fetchone()
    if not deployment:
        raise RuntimeError("Choose an existing AWS lab with an enabled cloud connection")
    credentials = decrypt_credentials(deployment["encrypted_credentials"], owner_id=str(deployment["owner_id"]))
    session = boto3.Session(aws_access_key_id=credentials["access_key_id"], aws_secret_access_key=credentials["secret_access_key"],
                            aws_session_token=credentials.get("session_token"), region_name=deployment["region"])
    identity = session.client("sts", config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 0})).get_caller_identity()
    if identity["Account"] != deployment["scope_id"]:
        raise RuntimeError("AWS identity does not match this lab's saved connection")
    if args.action == "deploy":
        with psycopg.connect(database_url, row_factory=dict_row) as database:
            queued = service.queue_upgrade(database, owner_id=str(deployment["owner_id"]), deployment_id=args.deployment_id)
            if not queued and deployment["status"] == "error" and deployment.get("terraform_state"):
                queued = service.queue_deploy(database, owner_id=str(deployment["owner_id"]),
                                              connection_id=str(deployment["connection_id"]), region=deployment["region"])
        if not queued:
            raise RuntimeError("The lab is not available for an upgrade")
        print(json.dumps({"phase": "deploying", "deployment_id": args.deployment_id}), flush=True)
        service.run(database_url, args.deployment_id, owner_id=str(deployment["owner_id"]), upgrade=True)
    with psycopg.connect(database_url, row_factory=dict_row) as database:
        result = database.execute("SELECT id, provider, region, status, outputs, last_error FROM lab_deployments WHERE id=%s AND owner_id=%s",
                                  (args.deployment_id, deployment["owner_id"])).fetchone()
    if result["status"] != "active":
        print(json.dumps(result, default=str), flush=True)
        raise SystemExit("Lab upgrade did not complete; persisted Terraform state is retained for recovery or Delete lab")
    if args.action in {"deploy", "seed"}:
        if not result["outputs"].get("paas"):
            raise RuntimeError("PaaS resources are not deployed yet")
        result["traffic_test"] = seed(session, result["outputs"]["paas"])
    print(json.dumps(result, default=str), flush=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, default=str)+"\n")


if __name__ == "__main__":
    main()
