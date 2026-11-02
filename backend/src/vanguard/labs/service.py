"""Ephemeral Terraform execution with state persisted per user in PostgreSQL."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from ..connections.credentials import decrypt_credentials

ROOT = Path(__file__).resolve().parents[4]
MODULE = ROOT / "infrastructure" / "lab"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS lab_deployments (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES cloud_connections(id) ON DELETE CASCADE,
    provider VARCHAR(16) NOT NULL CHECK (provider IN ('aws', 'gcp')),
    region TEXT NOT NULL,
    status VARCHAR(16) NOT NULL CHECK (status IN ('queued', 'deploying', 'active', 'destroying', 'error')),
    terraform_state JSONB,
    outputs JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (owner_id, connection_id)
);
CREATE INDEX IF NOT EXISTS lab_deployments_owner_idx ON lab_deployments (owner_id);
"""

# Only an explicit empty Terraform state proves a failed lab has no resources.
# Missing or malformed state remains protected until the lab is cleaned up.
CLEANUP_REQUIRED_SQL = """NOT COALESCE(
    status = 'error'
    AND terraform_state->'version' = '4'::jsonb
    AND terraform_state->'resources' = '[]'::jsonb,
    FALSE
)"""
PUBLIC_COLUMNS = (
    "id, connection_id, provider, region, status, outputs, last_error, created_at, updated_at, "
    + CLEANUP_REQUIRED_SQL + " AS cleanup_required"
)


def ensure_table(connection) -> None:
    connection.execute(SCHEMA_SQL)


def list_deployments(connection, owner_id: str) -> list[dict[str, Any]]:
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM lab_deployments WHERE owner_id = %s ORDER BY created_at",
            (owner_id,),
        ).fetchall()


def requires_cleanup(connection, connection_id: str, *, owner_id: str) -> bool:
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            f"SELECT {CLEANUP_REQUIRED_SQL} AS cleanup_required FROM lab_deployments "
            "WHERE connection_id = %s AND owner_id = %s FOR UPDATE",
            (connection_id, owner_id),
        ).fetchone()
    return bool(row and row["cleanup_required"])


def queue_deploy(connection, *, owner_id: str, connection_id: str, region: str) -> dict[str, Any]:
    cloud = connection.execute(
        "SELECT provider FROM cloud_connections WHERE id = %s AND owner_id = %s FOR UPDATE",
        (connection_id, owner_id),
    ).fetchone()
    if not cloud:
        raise LookupError("Cloud connection not found")
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            f"""
            INSERT INTO lab_deployments (id, owner_id, connection_id, provider, region, status)
            VALUES (%s, %s, %s, %s, %s, 'queued')
            ON CONFLICT (owner_id, connection_id) DO UPDATE SET
                region = EXCLUDED.region, status = 'queued', last_error = NULL, updated_at = NOW()
            WHERE lab_deployments.status = 'error'
              AND (lab_deployments.terraform_state IS NULL OR lab_deployments.region = EXCLUDED.region)
            RETURNING {PUBLIC_COLUMNS}
            """,
            (str(uuid4()), owner_id, connection_id, cloud["provider"], region),
        ).fetchone()
    if not row:
        raise RuntimeError("This connection already has a deployment")
    connection.commit()
    return row


class LabUpgradeConflict(RuntimeError):
    pass


def queue_upgrade(connection, *, owner_id: str, deployment_id: str) -> dict[str, Any] | None:
    """Claim an existing AWS lab without changing its identity or persisted state."""
    row = connection.execute(
        f"""UPDATE lab_deployments SET status = 'queued', last_error = NULL, updated_at = NOW()
        WHERE id = %s AND owner_id = %s AND provider = 'aws' AND status = 'active'
          AND EXISTS (SELECT 1 FROM cloud_connections c WHERE c.id = lab_deployments.connection_id
                      AND c.owner_id = lab_deployments.owner_id AND c.enabled)
        RETURNING {PUBLIC_COLUMNS}""",
        (deployment_id, owner_id),
    ).fetchone()
    connection.commit()
    return row


def stage_module(workdir: Path) -> None:
    for filename in ("main.tf", ".terraform.lock.hcl"):
        shutil.copy2(MODULE / filename, workdir / filename)
    # Stable archive bytes avoid needless Lambda updates on every deploy/destroy.
    info = zipfile.ZipInfo("handler.py", date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    with zipfile.ZipFile(workdir / "lambda.zip", "w") as archive:
        archive.writestr(info, (MODULE / "lambda_handler.py").read_bytes())


def existing_lab_ami(state: dict | None) -> str | None:
    """Pin the VM's actual image during an additive upgrade, despite most_recent data."""
    for resource in (state or {}).get("resources", []):
        if resource.get("type") != "aws_instance" or resource.get("name") != "lab":
            continue
        for instance in resource.get("instances", []):
            if instance.get("index_key") not in (None, 0):
                continue
            ami = (instance.get("attributes") or {}).get("ami")
            if isinstance(ami, str) and re.fullmatch(r"ami-[a-f0-9]+", ami):
                return ami
    return None


def verify_upgrade_plan(plan: dict) -> None:
    if not isinstance(plan, dict) or "planned_values" not in plan:
        raise LabUpgradeConflict("Terraform did not return a valid upgrade plan")
    for resource in plan.get("resource_changes", []):
        actions = resource.get("change", {}).get("actions") or []
        if not actions or set(actions) - {"create", "update", "read", "no-op"}:
            raise LabUpgradeConflict("Lab upgrade would delete or replace existing resources; no cloud changes applied")


def queue_destroy(connection, *, owner_id: str, deployment_id: str) -> dict[str, Any] | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            f"""
            UPDATE lab_deployments SET status = 'destroying', last_error = NULL, updated_at = NOW()
            WHERE id = %s AND owner_id = %s AND status IN ('active', 'error')
            RETURNING {PUBLIC_COLUMNS}
            """,
            (deployment_id, owner_id),
        ).fetchone()
    connection.commit()
    return row


def run(database_url: str, deployment_id: str, *, owner_id: str, destroy: bool = False, upgrade: bool = False) -> None:
    if destroy and upgrade:
        raise ValueError("A lab cannot be upgraded and destroyed together")
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5) as connection:
        deployment = connection.execute(
            """
            SELECT d.*, c.scope_id, c.encrypted_credentials
            FROM lab_deployments d JOIN cloud_connections c ON c.id = d.connection_id AND c.owner_id = d.owner_id
            WHERE d.id = %s AND d.owner_id = %s
            FOR UPDATE OF d
            """,
            (deployment_id, owner_id),
        ).fetchone()
        if not deployment:
            return
        if deployment["status"] != ("destroying" if destroy else "queued"):
            return
        connection.execute(
            "UPDATE lab_deployments SET status = %s, updated_at = NOW() WHERE id = %s",
            ("destroying" if destroy else "deploying", deployment_id),
        )
        connection.commit()

    try:
        credentials = decrypt_credentials(deployment["encrypted_credentials"], owner_id=str(deployment["owner_id"]))
    except Exception as exc:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            connection.execute(
                "UPDATE lab_deployments SET status = 'error', last_error = %s, updated_at = NOW() WHERE id = %s",
                (f"{type(exc).__name__}: saved credentials are unavailable", deployment_id),
            )
            connection.commit()
        return
    environment = {key: os.environ[key] for key in (
        "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR"
    ) if key in os.environ}
    environment["AWS_EC2_METADATA_DISABLED"] = "true"
    if deployment["provider"] == "aws":
        environment.update(
            AWS_ACCESS_KEY_ID=credentials["access_key_id"],
            AWS_SECRET_ACCESS_KEY=credentials["secret_access_key"],
        )
        if credentials.get("session_token"):
            environment["AWS_SESSION_TOKEN"] = credentials["session_token"]
    else:
        environment["GOOGLE_CREDENTIALS"] = json.dumps(credentials, separators=(",", ":"))
    environment["TF_IN_AUTOMATION"] = "1"
    plugin_cache = Path(
        os.getenv("VANGUARD_TERRAFORM_PLUGIN_CACHE", ROOT / ".vanguard" / "terraform-plugins")
    )
    plugin_cache.mkdir(parents=True, exist_ok=True)
    environment["TF_PLUGIN_CACHE_DIR"] = str(plugin_cache)

    state = deployment["terraform_state"]
    try:
        with tempfile.TemporaryDirectory(prefix="vanguard-lab-") as directory:
            workdir = Path(directory)
            stage_module(workdir)
            state_path = workdir / "terraform.tfstate"
            if deployment["terraform_state"]:
                state_path.write_text(json.dumps(deployment["terraform_state"]))
            variables = [
                f"-var=provider_name={deployment['provider']}",
                f"-var=deployment_id={str(deployment['id']).replace('-', '')[:10]}",
                f"-var=region={deployment['region']}",
                f"-var=gcp_project_id={deployment['scope_id']}",
            ]
            if upgrade and deployment["provider"] == "aws":
                ami = existing_lab_ami(deployment.get("terraform_state"))
                if ami:
                    variables.append(f"-var=aws_ami_id={ami}")
            subprocess.run(
                ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
                cwd=workdir, env=environment, check=True, capture_output=True, text=True,
                timeout=180,
            )
            command = "destroy" if destroy else "apply"
            arguments = [*variables]
            if upgrade:
                subprocess.run(
                    ["terraform", "plan", "-input=false", "-no-color", "-out=upgrade.tfplan", *variables],
                    cwd=workdir, env=environment, check=True, capture_output=True, text=True, timeout=300,
                )
                planned = subprocess.run(
                    ["terraform", "show", "-json", "upgrade.tfplan"], cwd=workdir, env=environment,
                    check=True, capture_output=True, text=True, timeout=30,
                )
                verify_upgrade_plan(json.loads(planned.stdout))
                arguments = ["upgrade.tfplan"]
            try:
                subprocess.run(
                    ["terraform", command, "-auto-approve", "-input=false", "-no-color", *arguments],
                    cwd=workdir, env=environment, check=True, capture_output=True, text=True,
                    timeout=900,
                )
            finally:
                # Terraform can create resources before returning an error. Keep
                # partial state so a later Destroy remains safe and complete.
                state = json.loads(state_path.read_text()) if state_path.exists() else state
            outputs = {}
            if not destroy:
                result = subprocess.run(
                    ["terraform", "output", "-json"], cwd=workdir, env=environment,
                    check=True, capture_output=True, text=True,
                    timeout=30,
                )
                outputs = {key: value.get("value") for key, value in json.loads(result.stdout).items()}
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            if destroy:
                connection.execute("DELETE FROM lab_deployments WHERE id = %s", (deployment_id,))
            else:
                connection.execute(
                    """
                    UPDATE lab_deployments SET status = 'active', terraform_state = %s::jsonb,
                        outputs = %s::jsonb, last_error = NULL, updated_at = NOW() WHERE id = %s
                    """,
                    (json.dumps(state), json.dumps(outputs), deployment_id),
                )
            connection.commit()
    except Exception as exc:
        message = (str(exc) if isinstance(exc, LabUpgradeConflict) else
                   f"{type(exc).__name__}: lab operation failed; check cloud permissions, quotas and configuration")
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            connection.execute(
                """
                UPDATE lab_deployments SET status = 'error', last_error = %s,
                    terraform_state = COALESCE(%s::jsonb, terraform_state), updated_at = NOW()
                WHERE id = %s
                """,
                (message, json.dumps(state) if state else None, deployment_id),
            )
            connection.commit()
