"""Persistence helpers for cloud accounts configured from the application."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from psycopg.rows import dict_row


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cloud_connections (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
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
    UNIQUE (owner_id, provider, scope_id)
);
ALTER TABLE cloud_connections ADD COLUMN IF NOT EXISTS owner_id UUID;
ALTER TABLE cloud_connections DROP CONSTRAINT IF EXISTS cloud_connections_provider_scope_id_key;
DO $$ BEGIN
    ALTER TABLE cloud_connections ADD CONSTRAINT cloud_connections_owner_fk
        FOREIGN KEY (owner_id) REFERENCES app_users(id) ON DELETE CASCADE NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE cloud_connections ADD CONSTRAINT cloud_connections_owner_required
        CHECK (owner_id IS NOT NULL) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS cloud_connections_owner_scope_uidx
    ON cloud_connections (owner_id, provider, scope_id);
CREATE INDEX IF NOT EXISTS cloud_connections_owner_idx ON cloud_connections (owner_id);
"""


PUBLIC_COLUMNS = """
id, owner_id, provider, name, scope_id, regions, credential_hint, identity, enabled,
status, last_error, last_tested_at, last_synced_at, created_at, updated_at
"""


def ensure_table(connection) -> None:
    connection.execute(SCHEMA_SQL)


def list_connections(
    connection, *, owner_id: str | None = None, enabled_only: bool = False
) -> list[dict[str, Any]]:
    clauses = []
    values: list[Any] = []
    if owner_id is not None:
        clauses.append("owner_id = %s")
        values.append(owner_id)
    if enabled_only:
        clauses.append("enabled")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM cloud_connections {where} ORDER BY created_at, name",
            values,
        ).fetchall()


def get_connection(
    connection, connection_id: str, *, owner_id: str | None = None, include_credentials: bool = False
):
    columns = f"{PUBLIC_COLUMNS}, encrypted_credentials" if include_credentials else PUBLIC_COLUMNS
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            f"SELECT {columns} FROM cloud_connections WHERE id = %s"
            + (" AND owner_id = %s" if owner_id is not None else ""),
            (connection_id, owner_id) if owner_id is not None else (connection_id,),
        ).fetchone()


def create_connection(
    connection,
    *,
    owner_id: str,
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
                id, owner_id, provider, name, scope_id, regions, credential_hint, identity,
                encrypted_credentials, status, last_tested_at
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, 'ready', %s)
            RETURNING {PUBLIC_COLUMNS}
            """,
            (
                connection_id, owner_id, provider, name, scope_id, json.dumps(regions),
                credential_hint, identity, encrypted_credentials, now,
            ),
        ).fetchone()
    connection.commit()
    return row


def update_connection(
    connection,
    connection_id: str,
    *,
    owner_id: str,
    name: str,
    regions: list[str],
    credential_hint: str,
    identity: str,
    encrypted_credentials: str,
) -> dict[str, Any] | None:
    ensure_table(connection)
    configured = connection.execute(
        "SELECT provider, scope_id, regions FROM cloud_connections WHERE id = %s AND owner_id = %s",
        (connection_id, owner_id),
    ).fetchone()
    if not configured:
        return None
    if configured["provider"] == "aws":
        removed_regions = list(set(configured["regions"]) - set(regions))
        if removed_regions:
            connection.execute(
                """
                DELETE FROM inventory_resources
                WHERE owner_id = %s AND provider = 'aws' AND scope_id = %s
                  AND region = ANY(%s::text[])
                """,
                (owner_id, configured["scope_id"], removed_regions),
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
            WHERE id = %s AND owner_id = %s
            RETURNING {PUBLIC_COLUMNS}
            """,
            (
                name, json.dumps(regions), credential_hint, identity,
                encrypted_credentials, connection_id, owner_id,
            ),
        ).fetchone()
    connection.commit()
    return row


def delete_connection(connection, connection_id: str, *, owner_id: str) -> bool:
    ensure_table(connection)
    configured = connection.execute(
        "SELECT provider, scope_id FROM cloud_connections WHERE id = %s AND owner_id = %s",
        (connection_id, owner_id),
    ).fetchone()
    if not configured:
        return False
    connection.execute(
        "DELETE FROM inventory_resources WHERE owner_id = %s AND provider = %s AND scope_id = %s",
        (owner_id, configured["provider"], configured["scope_id"]),
    )
    result = connection.execute(
        "DELETE FROM cloud_connections WHERE id = %s AND owner_id = %s", (connection_id, owner_id)
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


def claim_sync(connection, connection_id: str) -> bool:
    """Atomically claim a connection across API/worker instances."""
    result = connection.execute(
        """
        UPDATE cloud_connections
        SET status = 'syncing', updated_at = NOW()
        WHERE id = %s
          AND (status <> 'syncing' OR updated_at < NOW() - INTERVAL '15 minutes')
        RETURNING id
        """,
        (connection_id,),
    ).fetchone()
    connection.commit()
    return result is not None
