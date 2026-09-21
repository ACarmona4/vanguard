"""Validation and encrypted storage for user-provided cloud credentials."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


KEY_PATH = Path(".vanguard/credentials.key")


def _cipher() -> Fernet:
    path = Path(os.getenv("VANGUARD_CREDENTIAL_KEY_FILE", KEY_PATH)).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "wb") as key_file:
            key_file.write(Fernet.generate_key())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return Fernet(path.read_bytes().strip())


def encrypt_credentials(credentials: dict[str, Any]) -> str:
    payload = json.dumps(credentials, separators=(",", ":")).encode()
    return _cipher().encrypt(payload).decode()


def decrypt_credentials(value: str) -> dict[str, Any]:
    try:
        payload = _cipher().decrypt(value.encode())
        return json.loads(payload)
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("No fue posible descifrar las credenciales guardadas") from exc


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
        raise ValueError("El JSON debe contener una cuenta de servicio de GCP")
    google_credentials = gcp_credentials(credentials)
    project = resourcemanager_v3.ProjectsClient(
        credentials=google_credentials
    ).get_project(name=f"projects/{project_id}")
    return {
        "scope_id": project.project_id,
        "identity": credentials.get("client_email", "Cuenta de servicio"),
        "credential_hint": credentials.get("client_email", "Cuenta de servicio"),
    }
