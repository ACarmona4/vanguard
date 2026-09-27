"""Ephemeral Terraform execution with state persisted per user in PostgreSQL."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .credentials import decrypt_credentials


ROOT = Path(__file__).resolve().parents[4]
MODULE = ROOT / "infraestructure" / "lab"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS lab_deployments (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
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

PUBLIC_COLUMNS = "id, connection_id, provider, region, status, outputs, last_error, created_at, updated_at"


def ensure_table(connection) -> None:
    connection.execute(SCHEMA_SQL)


def list_deployments(connection, owner_id: str) -> list[dict[str, Any]]:
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM lab_deployments WHERE owner_id = %s ORDER BY created_at",
            (owner_id,),
        ).fetchall()


def queue_deploy(connection, *, owner_id: str, connection_id: str, region: str) -> dict[str, Any]:
    ensure_table(connection)
    cloud = connection.execute(
        "SELECT provider FROM cloud_connections WHERE id = %s AND owner_id = %s",
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
            RETURNING {PUBLIC_COLUMNS}
            """,
            (str(uuid4()), owner_id, connection_id, cloud["provider"], region),
        ).fetchone()
    if not row:
        raise RuntimeError("This connection already has a deployment")
    connection.commit()
    return row


def queue_destroy(connection, *, owner_id: str, deployment_id: str) -> dict[str, Any] | None:
    ensure_table(connection)
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


def _safe_error(error: subprocess.CalledProcessError, credentials: dict[str, Any]) -> str:
    text = (error.stderr or error.stdout or "Terraform operation failed").strip()
    for value in credentials.values():
        if isinstance(value, str) and len(value) >= 8:
            text = text.replace(value, "[redacted]")
    return text[-2000:]


def run(database_url: str, deployment_id: str, *, destroy: bool = False) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        ensure_table(connection)
        deployment = connection.execute(
            """
            SELECT d.*, c.scope_id, c.encrypted_credentials
            FROM lab_deployments d JOIN cloud_connections c ON c.id = d.connection_id
            WHERE d.id = %s
            """,
            (deployment_id,),
        ).fetchone()
        if not deployment:
            return
        connection.execute(
            "UPDATE lab_deployments SET status = %s, updated_at = NOW() WHERE id = %s",
            ("destroying" if destroy else "deploying", deployment_id),
        )
        connection.commit()

    try:
        credentials = decrypt_credentials(deployment["encrypted_credentials"])
    except Exception as exc:
        with psycopg.connect(database_url) as connection:
            connection.execute(
                "UPDATE lab_deployments SET status = 'error', last_error = %s, updated_at = NOW() WHERE id = %s",
                (f"{type(exc).__name__}: saved credentials are unavailable", deployment_id),
            )
            connection.commit()
        return
    environment = os.environ.copy()
    for key in (
        "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "GOOGLE_CREDENTIALS",
    ):
        environment.pop(key, None)
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
            shutil.copy2(MODULE / "main.tf", workdir / "main.tf")
            shutil.copy2(MODULE / ".terraform.lock.hcl", workdir / ".terraform.lock.hcl")
            state_path = workdir / "terraform.tfstate"
            if deployment["terraform_state"]:
                state_path.write_text(json.dumps(deployment["terraform_state"]))
            variables = [
                f"-var=provider_name={deployment['provider']}",
                f"-var=deployment_id={str(deployment['id']).replace('-', '')[:10]}",
                f"-var=region={deployment['region']}",
                f"-var=gcp_project_id={deployment['scope_id']}",
            ]
            subprocess.run(
                ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
                cwd=workdir, env=environment, check=True, capture_output=True, text=True,
            )
            command = "destroy" if destroy else "apply"
            try:
                subprocess.run(
                    ["terraform", command, "-auto-approve", "-input=false", "-no-color", *variables],
                    cwd=workdir, env=environment, check=True, capture_output=True, text=True,
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
                )
                outputs = {key: value.get("value") for key, value in json.loads(result.stdout).items()}
        with psycopg.connect(database_url) as connection:
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
        message = _safe_error(exc, credentials) if isinstance(exc, subprocess.CalledProcessError) else f"{type(exc).__name__}: {exc}"
        with psycopg.connect(database_url) as connection:
            connection.execute(
                """
                UPDATE lab_deployments SET status = 'error', last_error = %s,
                    terraform_state = COALESCE(%s::jsonb, terraform_state), updated_at = NOW()
                WHERE id = %s
                """,
                (message, json.dumps(state) if state else None, deployment_id),
            )
            connection.commit()
