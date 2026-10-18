"""Validation and encrypted storage for cloud account credentials."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

LOCAL_VAULT_KEY = Path(".vanguard/vault.key")
GCP_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
GCP_TOKEN_URIS = {"https://oauth2.googleapis.com/token", "https://accounts.google.com/o/oauth2/token"}


class CredentialValidationError(ValueError):
    """A safe credential-format error that can be returned to the user."""


def _cipher() -> Fernet:
    configured_key = os.getenv("VANGUARD_MASTER_KEY")
    if configured_key:
        try:
            return Fernet(configured_key.encode())
        except (TypeError, ValueError) as exc:
            raise RuntimeError("VANGUARD_MASTER_KEY is invalid") from exc

    environment = os.getenv("VANGUARD_ENVIRONMENT", "development").strip().lower()
    if environment == "production":
        raise RuntimeError(
            "Credential encryption is not configured. Provide VANGUARD_MASTER_KEY "
            "through the deployment secret manager."
        )

    # Local development is zero-configuration. This installation key is not a
    # cloud credential and never leaves the machine; cloud secrets remain only
    # inside encrypted database records.
    path = Path(os.getenv("VANGUARD_LOCAL_VAULT_KEY_FILE", LOCAL_VAULT_KEY)).expanduser()
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
        return Fernet(path.read_bytes().strip())
    except (OSError, ValueError) as exc:
        raise RuntimeError("The local credential vault could not be initialized") from exc


def encrypt_credentials(credentials: dict[str, Any], *, owner_id: str) -> str:
    if not owner_id:
        raise ValueError("An organization is required")
    payload = json.dumps({"organization_id": str(owner_id), "credentials": credentials}, separators=(",", ":")).encode()
    return _cipher().encrypt(payload).decode()


def decrypt_credentials(value: str, *, owner_id: str) -> dict[str, Any]:
    if not owner_id:
        raise ValueError("An organization is required")
    try:
        payload = _cipher().decrypt(value.encode())
        decoded = json.loads(payload)
        if "organization_id" in decoded:
            if decoded["organization_id"] != str(owner_id):
                raise ValueError("Credential organization mismatch")
            return decoded["credentials"]
        # Existing installations keep working until credentials are next saved.
        return decoded
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("The saved credentials could not be decrypted") from exc


def validate_aws(credentials: dict[str, str], regions: list[str]) -> dict[str, str]:
    import boto3
    from botocore.config import Config

    session = boto3.Session(
        aws_access_key_id=credentials["access_key_id"],
        aws_secret_access_key=credentials["secret_access_key"],
        aws_session_token=credentials.get("session_token") or None,
        region_name=regions[0],
    )
    identity = session.client("sts", region_name=regions[0], config=Config(
        connect_timeout=5, read_timeout=10, retries={"max_attempts": 1}
    )).get_caller_identity()
    return {
        "scope_id": identity["Account"],
        "identity": identity["Arn"],
        "credential_hint": f"{credentials['access_key_id'][:4]}…{credentials['access_key_id'][-4:]}",
    }


def gcp_credentials(credentials: dict[str, Any]):
    credential_type = credentials.get("type")
    if (credentials.get("token_uri", "https://oauth2.googleapis.com/token") not in GCP_TOKEN_URIS
            or credentials.get("universe_domain", "googleapis.com") != "googleapis.com"):
        raise CredentialValidationError("Use Google OAuth endpoints and the googleapis.com universe.")
    try:
        if credential_type == "service_account":
            from google.oauth2 import service_account

            return service_account.Credentials.from_service_account_info(
                credentials, scopes=[GCP_SCOPE]
            )
        if credential_type == "authorized_user":
            from google.oauth2.credentials import Credentials

            return Credentials.from_authorized_user_info(
                credentials, scopes=[GCP_SCOPE]
            )
    except (KeyError, ValueError) as exc:
        raise CredentialValidationError(
            "The GCP credential JSON is incomplete or invalid."
        ) from exc
    raise CredentialValidationError(
        "Use a GCP service account key or Application Default Credentials JSON."
    )


def validate_gcp(credentials: dict[str, Any], project_id: str) -> dict[str, str]:
    from google.cloud import resourcemanager_v3

    google_credentials = gcp_credentials(credentials)
    project = resourcemanager_v3.ProjectsClient(
        credentials=google_credentials
    ).get_project(name=f"projects/{project_id}", timeout=15, retry=None)
    identity = (
        credentials.get("client_email")
        or credentials.get("account")
        or "Google user credentials"
    )
    return {
        "scope_id": project.project_id,
        "identity": identity,
        "credential_hint": identity,
    }
