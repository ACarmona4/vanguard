"""Validation and encrypted storage for cloud account credentials."""

from __future__ import annotations

import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


def _cipher() -> Fernet:
    try:
        return Fernet(os.environ["VANGUARD_MASTER_KEY"].encode())
    except KeyError as exc:
        raise RuntimeError(
            "Credential encryption is not configured. Provide VANGUARD_MASTER_KEY "
            "through the deployment secret manager, never in .env."
        ) from exc
    except (TypeError, ValueError) as exc:
        raise RuntimeError("VANGUARD_MASTER_KEY is invalid") from exc


def encrypt_credentials(credentials: dict[str, Any]) -> str:
    payload = json.dumps(credentials, separators=(",", ":")).encode()
    return _cipher().encrypt(payload).decode()


def decrypt_credentials(value: str) -> dict[str, Any]:
    try:
        payload = _cipher().decrypt(value.encode())
        return json.loads(payload)
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("The saved credentials could not be decrypted") from exc


def validate_aws(credentials: dict[str, str], regions: list[str]) -> dict[str, str]:
    import boto3

    session = boto3.Session(
        aws_access_key_id=credentials["access_key_id"],
        aws_secret_access_key=credentials["secret_access_key"],
        aws_session_token=credentials.get("session_token") or None,
        region_name=regions[0],
    )
    identity = session.client("sts", region_name=regions[0]).get_caller_identity()
    return {
        "scope_id": identity["Account"],
        "identity": identity["Arn"],
        "credential_hint": f"{credentials['access_key_id'][:4]}…{credentials['access_key_id'][-4:]}",
    }


def gcp_credentials(credentials: dict[str, Any]):
    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_info(
        credentials,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def validate_gcp(credentials: dict[str, Any], project_id: str) -> dict[str, str]:
    from google.cloud import resourcemanager_v3

    if credentials.get("type") != "service_account":
        raise ValueError("The JSON must contain a GCP service account")
    google_credentials = gcp_credentials(credentials)
    project = resourcemanager_v3.ProjectsClient(
        credentials=google_credentials
    ).get_project(name=f"projects/{project_id}")
    return {
        "scope_id": project.project_id,
        "identity": credentials.get("client_email", "Service account"),
        "credential_hint": credentials.get("client_email", "Service account"),
    }
