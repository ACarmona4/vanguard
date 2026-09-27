from __future__ import annotations

from collections.abc import Iterable
import json
from functools import partial

from .models import Resource
from .serialization import json_default

_json_dumps = partial(json.dumps, default=json_default)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS inventory_resources (
    id BIGSERIAL PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES cloud_connections(id) ON DELETE CASCADE,
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
    UNIQUE (owner_id, provider, resource_type, resource_id)
);
ALTER TABLE inventory_resources ADD COLUMN IF NOT EXISTS owner_id UUID;
ALTER TABLE inventory_resources ADD COLUMN IF NOT EXISTS connection_id UUID;
ALTER TABLE inventory_resources DROP CONSTRAINT IF EXISTS inventory_resources_provider_resource_type_resource_id_key;
DO $$ BEGIN
    ALTER TABLE inventory_resources ADD CONSTRAINT inventory_resources_owner_fk
        FOREIGN KEY (owner_id) REFERENCES app_users(id) ON DELETE CASCADE NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE inventory_resources ADD CONSTRAINT inventory_resources_connection_fk
        FOREIGN KEY (connection_id) REFERENCES cloud_connections(id) ON DELETE CASCADE NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE inventory_resources ADD CONSTRAINT inventory_resources_owner_required
        CHECK (owner_id IS NOT NULL) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE inventory_resources ADD CONSTRAINT inventory_resources_connection_required
        CHECK (connection_id IS NOT NULL) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS inventory_resources_owner_resource_uidx
    ON inventory_resources (owner_id, provider, resource_type, resource_id);
CREATE INDEX IF NOT EXISTS inventory_resources_owner_idx
    ON inventory_resources (owner_id);
CREATE INDEX IF NOT EXISTS inventory_resources_owner_scope_idx
    ON inventory_resources (owner_id, provider, scope_id);
CREATE INDEX IF NOT EXISTS inventory_resources_owner_type_idx
    ON inventory_resources (owner_id, provider, resource_type);
"""

UPSERT_SQL = """
INSERT INTO inventory_resources (
    owner_id, connection_id, provider, resource_type, resource_id, name, scope_id, region, zone,
    status, attributes, raw_data, first_seen_at, last_seen_at
) VALUES (
    %(owner_id)s, %(connection_id)s, %(provider)s, %(resource_type)s, %(resource_id)s, %(name)s, %(scope_id)s,
    %(region)s, %(zone)s, %(status)s, %(attributes)s, %(raw_data)s,
    %(collected_at)s, %(collected_at)s
)
ON CONFLICT (owner_id, provider, resource_type, resource_id) DO UPDATE SET
    connection_id = EXCLUDED.connection_id,
    name = EXCLUDED.name,
    scope_id = EXCLUDED.scope_id,
    region = EXCLUDED.region,
    zone = EXCLUDED.zone,
    status = EXCLUDED.status,
    attributes = EXCLUDED.attributes,
    raw_data = EXCLUDED.raw_data,
    last_seen_at = EXCLUDED.last_seen_at;
"""

CREATE_SNAPSHOT_KEYS_SQL = """
CREATE TEMP TABLE current_inventory_keys (
    resource_type VARCHAR(128) NOT NULL,
    resource_id TEXT NOT NULL,
    PRIMARY KEY (resource_type, resource_id)
) ON COMMIT DROP;
"""

INSERT_SNAPSHOT_KEY_SQL = """
INSERT INTO current_inventory_keys (resource_type, resource_id)
VALUES (%(resource_type)s, %(resource_id)s)
ON CONFLICT DO NOTHING;
"""

DELETE_STALE_SQL = """
DELETE FROM inventory_resources AS stored
WHERE stored.provider = %(provider)s
  AND stored.owner_id = %(owner_id)s
  AND stored.connection_id = %(connection_id)s
  AND stored.scope_id = %(scope_id)s
  AND (%(regions)s::text[] IS NULL OR stored.region = ANY(%(regions)s::text[]))
  AND NOT EXISTS (
      SELECT 1
      FROM current_inventory_keys AS current
      WHERE current.resource_type = stored.resource_type
        AND current.resource_id = stored.resource_id
  );
"""


def ensure_schema(connection) -> None:
    connection.execute(SCHEMA_SQL)


def _upsert(cursor, rows: list[Resource], Jsonb, *, owner_id: str, connection_id: str) -> None:
    for resource in rows:
        cursor.execute(
            UPSERT_SQL,
            {
                "owner_id": owner_id,
                "connection_id": connection_id,
                "provider": resource.provider,
                "resource_type": resource.resource_type,
                "resource_id": resource.resource_id,
                "name": resource.name,
                "scope_id": resource.scope_id,
                "region": resource.region,
                "zone": resource.zone,
                "status": resource.status,
                "attributes": Jsonb(resource.attributes, dumps=_json_dumps),
                "raw_data": Jsonb(resource.raw_data, dumps=_json_dumps),
                "collected_at": resource.collected_at,
            },
        )


def save_resources(
    database_url: str, resources: Iterable[Resource], *, owner_id: str, connection_id: str
) -> int:
    import psycopg
    from psycopg.types.json import Jsonb

    rows = list(resources)
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            ensure_schema(cursor)
            _upsert(cursor, rows, Jsonb, owner_id=owner_id, connection_id=connection_id)
        connection.commit()
    return len(rows)


def sync_resources(
    database_url: str,
    resources: Iterable[Resource],
    *,
    provider: str,
    scope_id: str,
    owner_id: str,
    connection_id: str,
    regions: Iterable[str] | None = None,
) -> tuple[int, int]:
    """Persist a complete snapshot and remove records absent from its scope.

    Call this only after every collector for the requested scope completed
    successfully. The UPSERT and reconciliation run in one transaction.
    """
    import psycopg
    from psycopg.types.json import Jsonb

    rows = list(resources)
    region_list = list(dict.fromkeys(regions)) if regions is not None else None
    if any(resource.provider != provider or resource.scope_id != scope_id for resource in rows):
        raise ValueError("All resources must belong to the reconciled provider and scope")
    if region_list is not None:
        allowed_regions = set(region_list)
        if any(resource.region not in allowed_regions for resource in rows):
            raise ValueError("All resources must belong to the reconciled regions")
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            ensure_schema(cursor)
            cursor.execute(CREATE_SNAPSHOT_KEYS_SQL)
            _upsert(cursor, rows, Jsonb, owner_id=owner_id, connection_id=connection_id)
            for resource in rows:
                cursor.execute(
                    INSERT_SNAPSHOT_KEY_SQL,
                    {
                        "resource_type": resource.resource_type,
                        "resource_id": resource.resource_id,
                    },
                )
            cursor.execute(
                DELETE_STALE_SQL,
                {
                    "provider": provider,
                    "scope_id": scope_id,
                    "owner_id": owner_id,
                    "connection_id": connection_id,
                    "regions": region_list,
                },
            )
            deleted = cursor.rowcount
        connection.commit()
    return len(rows), deleted
