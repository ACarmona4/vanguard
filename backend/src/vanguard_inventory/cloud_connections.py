"""Persistence helpers for cloud connections configured from the application."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from psycopg.rows import dict_row


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cloud_connections (
    id UUID PRIMARY KEY,
    provider VARCHAR(16) NOT NULL CHECK (provider IN ('aws', 'gcp')),
    name VARCHAR(120) NOT NULL,
    scope_id TEXT NOT NULL,
    regions JSONB NOT NULL DEFAULT '[]'::jsonb,
    credential_hint TEXT NOT NULL,
    identity TEXT NOT NULL,
    encrypted_credentials TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR(16) NOT NULL DEFAULT 'ready',
    last_error TEXT,
    last_tested_at TIMESTAMPTZ,
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, scope_id)
);
"""


PUBLIC_COLUMNS = """
id, provider, name, scope_id, regions, credential_hint, identity, enabled,
status, last_error, last_tested_at, last_synced_at, created_at, updated_at
"""


def ensure_table(connection) -> None:
    connection.execute(SCHEMA_SQL)


def list_connections(connection, *, enabled_only: bool = False) -> list[dict[str, Any]]:
    where = "WHERE enabled" if enabled_only else ""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM cloud_connections {where} ORDER BY created_at, name"
        ).fetchall()


def get_connection(connection, connection_id: str, *, include_credentials: bool = False):
    columns = f"{PUBLIC_COLUMNS}, encrypted_credentials" if include_credentials else PUBLIC_COLUMNS
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            f"SELECT {columns} FROM cloud_connections WHERE id = %s",
            (connection_id,),
        ).fetchone()


def create_connection(
    connection,
    *,
    provider: str,
    name: str,
    scope_id: str,
    regions: list[str],
    credential_hint: str,
    identity: str,
    encrypted_credentials: str,
) -> dict[str, Any]:
    ensure_table(connection)
    connection_id = str(uuid4())
    now = datetime.now(timezone.utc)
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            f"""
            INSERT INTO cloud_connections (
                id, provider, name, scope_id, regions, credential_hint, identity,
                encrypted_credentials, status, last_tested_at
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, 'ready', %s)
            RETURNING {PUBLIC_COLUMNS}
            """,
            (
                connection_id, provider, name, scope_id, json.dumps(regions),
                credential_hint, identity, encrypted_credentials, now,
            ),
        ).fetchone()
    connection.commit()
    return row


def update_connection(
    connection,
    connection_id: str,
    *,
    name: str,
    regions: list[str],
    credential_hint: str,
    identity: str,
    encrypted_credentials: str,
) -> dict[str, Any] | None:
    ensure_table(connection)
    configured = connection.execute(
        "SELECT provider, scope_id, regions FROM cloud_connections WHERE id = %s",
        (connection_id,),
    ).fetchone()
    if not configured:
        return None
    if configured[0] == "aws":
        removed_regions = list(set(configured[2]) - set(regions))
        if removed_regions:
            connection.execute(
                """
                DELETE FROM inventory_resources
                WHERE provider = 'aws' AND scope_id = %s AND region = ANY(%s::text[])
                """,
                (configured[1], removed_regions),
            )
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            f"""
            UPDATE cloud_connections
            SET name = %s,
                regions = %s::jsonb,
                credential_hint = %s,
                identity = %s,
                encrypted_credentials = %s,
                status = 'ready',
                last_error = NULL,
                last_tested_at = NOW(),
                updated_at = NOW()
            WHERE id = %s
            RETURNING {PUBLIC_COLUMNS}
            """,
            (
                name, json.dumps(regions), credential_hint, identity,
                encrypted_credentials, connection_id,
            ),
        ).fetchone()
    connection.commit()
    return row


def delete_connection(connection, connection_id: str) -> bool:
    ensure_table(connection)
    configured = connection.execute(
        "SELECT provider, scope_id FROM cloud_connections WHERE id = %s",
        (connection_id,),
    ).fetchone()
    if not configured:
        return False
    connection.execute(
        "DELETE FROM inventory_resources WHERE provider = %s AND scope_id = %s",
        (configured[0], configured[1]),
    )
    result = connection.execute(
        "DELETE FROM cloud_connections WHERE id = %s", (connection_id,)
    )
    connection.commit()
    return result.rowcount > 0


def set_connection_status(
    connection,
    connection_id: str,
    *,
    status: str,
    error: str | None = None,
    synced: bool = False,
) -> None:
    ensure_table(connection)
    connection.execute(
        """
        UPDATE cloud_connections
        SET status = %s,
            last_error = %s,
            last_synced_at = CASE WHEN %s THEN NOW() ELSE last_synced_at END,
            updated_at = NOW()
        WHERE id = %s
        """,
        (status, error, synced, connection_id),
    )
    connection.commit()
