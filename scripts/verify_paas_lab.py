"""Opt-in AWS lab smoke test. Creates one tagged Lambda, observes real metrics, then cleans up.

PYTHONPATH=backend/src .venv/bin/python scripts/verify_paas_lab.py --connection-id UUID
Uses saved encrypted connection credentials. Never prints or writes cloud credentials.
An existing Lambda execution role (or --role-arn) is required; IAM policies are not changed.
"""

import argparse
import io
import json
import os
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import boto3
import psycopg
from botocore.config import Config
from psycopg.rows import dict_row
from vanguard.connections.credentials import decrypt_credentials
from vanguard.core.config import load_environment
from vanguard.utilization.cloud import aws_samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-id", required=True)
    parser.add_argument("--role-arn")
    parser.add_argument("--max-wait-seconds", type=int, default=600)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    load_environment()
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as database:
        cloud = database.execute("SELECT * FROM cloud_connections WHERE id = %s AND provider = 'aws' AND enabled AND owner_id IS NOT NULL", (args.connection_id,)).fetchone()
    if not cloud:
        raise RuntimeError("Choose an enabled AWS connection owned by a workspace")
    credentials = decrypt_credentials(cloud["encrypted_credentials"], owner_id=str(cloud["owner_id"]))
    region = (cloud["regions"] or ["us-east-1"])[0]
    session = boto3.Session(aws_access_key_id=credentials["access_key_id"], aws_secret_access_key=credentials["secret_access_key"],
                            aws_session_token=credentials.get("session_token"), region_name=region)
    config = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 1})
    account = session.client("sts", config=config).get_caller_identity()["Account"]
    if account != cloud["scope_id"]:
        raise RuntimeError("AWS identity does not match the selected connection")
    client = session.client("lambda", config=config)
    role = args.role_arn
    if not role:
        existing = [function for page in client.get_paginator("list_functions").paginate() for function in page["Functions"]]
        role = next((function["Role"] for function in existing if function.get("Role", "").endswith("/LabRole")), None)
    if not role:
        raise RuntimeError("Provide an existing execution role with --role-arn")
    name = "vanguard-paas-check-" + uuid4().hex[:12]
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("handler.py", 'def handler(event, context):\n    if event.get("fail"):\n        raise RuntimeError("Vanguard controlled lab failure")\n    return {"ok": True}\n')
    report = {"account": account, "region": region, "function": name, "created": False, "metrics_observed": [], "cleanup": {}}
    try:
        result = client.create_function(FunctionName=name, Runtime="python3.12", Role=role, Handler="handler.handler",
                                        Code={"ZipFile": archive.getvalue()}, MemorySize=128, Timeout=5,
                                        Tags={"ManagedBy": "vanguard", "Disposable": "true", "Purpose": "paas-verification"})
        report["created"] = True
        report["arn"] = result["FunctionArn"]
        print(json.dumps({"phase": "created", "function": name}), flush=True)
        client.get_waiter("function_active_v2").wait(FunctionName=name, WaiterConfig={"Delay": 2, "MaxAttempts": 30})
        outcomes = []
        for fail in (False, False, False, True, True):
            response = client.invoke(FunctionName=name, Payload=json.dumps({"fail": fail}).encode())
            response["Payload"].read()
            outcomes.append("error" if response.get("FunctionError") else "success")
        report["invocations"] = outcomes
        resource = {"id": 0, "owner_id": str(cloud["owner_id"]), "provider": "aws", "scope_id": account, "region": region,
                    "resource_type": "lambda_function", "resource_id": result["FunctionArn"], "name": name,
                    "attributes": {"timeout_seconds": 5, "provisioned_concurrency": 0}}
        deadline = time.monotonic() + max(0, min(args.max_wait_seconds, 900))
        while True:
            samples, errors = aws_samples([resource], credentials, datetime.now(timezone.utc))
            report["metrics_observed"] = sorted({metric.key for _, metric, _, _ in samples})
            report["metric_errors"] = errors
            if {"invocations_per_second", "errors_per_second", "duration_p95_ms"} <= set(report["metrics_observed"]):
                report["status"] = "passed"
                break
            if time.monotonic() >= deadline:
                report["status"] = "metric_publication_pending"
                break
            time.sleep(15)
        print(json.dumps({"phase": "observed", "status": report["status"], "metrics": report["metrics_observed"]}), flush=True)
    finally:
        if report["created"]:
            try:
                tags = client.list_tags(Resource=report["arn"])["Tags"]
                if tags.get("Purpose") != "paas-verification" or tags.get("Disposable") != "true":
                    raise RuntimeError("Temporary resource tags changed; cleanup refused")
                client.delete_function(FunctionName=name)
                report["cleanup"]["function_deleted"] = True
                logs = session.client("logs", config=config)
                try:
                    logs.delete_log_group(logGroupName="/aws/lambda/" + name)
                    report["cleanup"]["logs_deleted"] = True
                except logs.exceptions.ResourceNotFoundException:
                    report["cleanup"]["logs_deleted"] = True
            except Exception as exc:
                report["cleanup"]["error"] = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"phase": "cleanup", **report["cleanup"]}), flush=True)
    if report.get("status") != "passed" or not report["cleanup"].get("function_deleted"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
