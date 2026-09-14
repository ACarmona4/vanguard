from __future__ import annotations

from collections.abc import Iterable

from .models import Resource


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS inventory_resources (
    id BIGSERIAL PRIMARY KEY,
    provider VARCHAR(32) NOT NULL,
    resource_type VARCHAR(128) NOT NULL,
    resource_id TEXT NOT NULL,
    name TEXT,
    scope_id TEXT,
    region TEXT,
    zone TEXT,
    status TEXT,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_data JSONB NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    UNIQUE (provider, resource_type, resource_id)
);
CREATE INDEX IF NOT EXISTS inventory_resources_scope_idx
    ON inventory_resources (provider, scope_id);
CREATE INDEX IF NOT EXISTS inventory_resources_type_idx
    ON inventory_resources (provider, resource_type);
"""

UPSERT_SQL = """
INSERT INTO inventory_resources (
    provider, resource_type, resource_id, name, scope_id, region, zone,
    status, attributes, raw_data, first_seen_at, last_seen_at
) VALUES (
    %(provider)s, %(resource_type)s, %(resource_id)s, %(name)s, %(scope_id)s,
    %(region)s, %(zone)s, %(status)s, %(attributes)s, %(raw_data)s,
    %(collected_at)s, %(collected_at)s
)
ON CONFLICT (provider, resource_type, resource_id) DO UPDATE SET
    name = EXCLUDED.name,
    scope_id = EXCLUDED.scope_id,
    region = EXCLUDED.region,
    zone = EXCLUDED.zone,
    status = EXCLUDED.status,
    attributes = EXCLUDED.attributes,
    raw_data = EXCLUDED.raw_data,
    last_seen_at = EXCLUDED.last_seen_at;
"""


def save_resources(database_url: str, resources: Iterable[Resource]) -> int:
    import psycopg
    from psycopg.types.json import Jsonb

    rows = list(resources)
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)
            for resource in rows:
                cursor.execute(
                    UPSERT_SQL,
                    {
                        "provider": resource.provider,
                        "resource_type": resource.resource_type,
                        "resource_id": resource.resource_id,
                        "name": resource.name,
                        "scope_id": resource.scope_id,
                        "region": resource.region,
                        "zone": resource.zone,
                        "status": resource.status,
                        "attributes": Jsonb(resource.attributes),
                        "raw_data": Jsonb(resource.raw_data),
                        "collected_at": resource.collected_at,
                    },
                )
        connection.commit()
    return len(rows)


def check_database(database_url: str) -> str:
    import psycopg

    with psycopg.connect(database_url, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_user")
            database, user = cursor.fetchone()
    return f"PostgreSQL disponible: database={database}, user={user}"

