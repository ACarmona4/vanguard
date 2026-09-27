"""Persistence and tenant-safe aggregate queries for cloud costs."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from psycopg.rows import dict_row


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cost_sources (
    connection_id UUID PRIMARY KEY REFERENCES cloud_connections(id) ON DELETE CASCADE,
    owner_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    provider VARCHAR(16) NOT NULL CHECK (provider IN ('aws', 'gcp')),
    billing_export_table TEXT,
    billing_location VARCHAR(32),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR(16) NOT NULL CHECK (status IN ('unconfigured', 'ready', 'syncing', 'success', 'error')),
    detail_available BOOLEAN NOT NULL DEFAULT FALSE,
    last_error TEXT,
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (owner_id, connection_id)
);
CREATE INDEX IF NOT EXISTS cost_sources_owner_idx ON cost_sources (owner_id);

CREATE TABLE IF NOT EXISTS cost_records (
    id BIGSERIAL PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES cloud_connections(id) ON DELETE CASCADE,
    usage_date DATE NOT NULL,
    provider VARCHAR(16) NOT NULL CHECK (provider IN ('aws', 'gcp')),
    scope_id TEXT NOT NULL,
    level VARCHAR(16) NOT NULL CHECK (level IN ('service', 'resource')),
    service TEXT NOT NULL,
    category VARCHAR(32) NOT NULL,
    resource_id TEXT,
    resource_name TEXT,
    region TEXT,
    currency VARCHAR(8) NOT NULL,
    amount NUMERIC(24, 8) NOT NULL,
    estimated BOOLEAN NOT NULL DEFAULT FALSE,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS cost_records_owner_level_period_idx
    ON cost_records (owner_id, level, usage_date);
CREATE INDEX IF NOT EXISTS cost_records_connection_period_idx
    ON cost_records (connection_id, usage_date);
"""

SOURCE_COLUMNS = """
s.connection_id, s.provider, c.name AS connection_name, c.scope_id,
s.billing_export_table, s.billing_location, s.enabled, s.status,
s.detail_available, s.last_error, s.last_synced_at, s.updated_at
"""


def ensure_schema(connection) -> None:
    connection.execute(SCHEMA_SQL)
    connection.execute("""
        INSERT INTO cost_sources (connection_id, owner_id, provider, status)
        SELECT id, owner_id, provider,
               CASE WHEN provider = 'aws' THEN 'ready' ELSE 'unconfigured' END
        FROM cloud_connections
        WHERE owner_id IS NOT NULL
        ON CONFLICT (connection_id) DO NOTHING
    """)


def ensure_source(connection, cloud: dict) -> None:
    connection.execute("""
        INSERT INTO cost_sources (connection_id, owner_id, provider, status)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (connection_id) DO NOTHING
    """, (
        cloud["id"], cloud["owner_id"], cloud["provider"],
        "ready" if cloud["provider"] == "aws" else "unconfigured",
    ))


def list_sources(connection, owner_id: str) -> list[dict[str, Any]]:
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            f"""
            SELECT {SOURCE_COLUMNS}
            FROM cost_sources s JOIN cloud_connections c ON c.id = s.connection_id
            WHERE s.owner_id = %s ORDER BY c.created_at, c.name
            """,
            (owner_id,),
        ).fetchall()


def configure_gcp_source(
    connection,
    *,
    owner_id: str,
    connection_id: str,
    billing_export_table: str,
    billing_location: str,
) -> dict[str, Any] | None:
    existing = connection.execute(
        "SELECT billing_export_table FROM cost_sources WHERE connection_id = %s AND owner_id = %s",
        (connection_id, owner_id),
    ).fetchone()
    if existing and existing["billing_export_table"] != billing_export_table:
        connection.execute(
            "DELETE FROM cost_records WHERE connection_id = %s AND owner_id = %s",
            (connection_id, owner_id),
        )
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            f"""
            UPDATE cost_sources s SET
                billing_export_table = %s, billing_location = %s,
                status = 'ready', last_error = NULL, updated_at = NOW()
            FROM cloud_connections c
            WHERE s.connection_id = c.id AND s.connection_id = %s
              AND s.owner_id = %s AND s.provider = 'gcp'
            RETURNING {SOURCE_COLUMNS}
            """,
            (billing_export_table, billing_location, connection_id, owner_id),
        ).fetchone()
    connection.commit()
    return row


def claim_source(connection, connection_id: str, *, force: bool = False) -> bool:
    result = connection.execute("""
        UPDATE cost_sources SET status = 'syncing', last_error = NULL, updated_at = NOW()
        WHERE connection_id = %s AND enabled
          AND status <> 'unconfigured'
          AND (status <> 'syncing' OR updated_at < NOW() - INTERVAL '30 minutes')
          AND (%s OR last_synced_at IS NULL OR last_synced_at < NOW() - INTERVAL '5 hours')
        RETURNING connection_id
    """, (connection_id, force)).fetchone()
    connection.commit()
    return result is not None


def save_records(
    connection,
    *,
    source: dict,
    records: list[dict[str, Any]],
    start: date,
    end: date,
    detail_available: bool,
) -> None:
    connection.execute("""
        DELETE FROM cost_records
        WHERE connection_id = %s AND owner_id = %s
          AND usage_date >= %s AND usage_date < %s
    """, (source["connection_id"], source["owner_id"], start, end))
    if records:
        with connection.cursor() as cursor:
            cursor.executemany("""
                INSERT INTO cost_records (
                    owner_id, connection_id, usage_date, provider, scope_id, level,
                    service, category, resource_id, resource_name, region,
                    currency, amount, estimated
                ) VALUES (
                    %(owner_id)s, %(connection_id)s, %(usage_date)s, %(provider)s,
                    %(scope_id)s, %(level)s, %(service)s, %(category)s,
                    %(resource_id)s, %(resource_name)s, %(region)s,
                    %(currency)s, %(amount)s, %(estimated)s
                )
            """, records)
    connection.execute("""
        UPDATE cost_sources SET status = 'success', detail_available = %s,
            last_error = NULL, last_synced_at = NOW(), updated_at = NOW()
        WHERE connection_id = %s
    """, (detail_available, source["connection_id"]))
    connection.commit()


def set_source_error(connection, connection_id: str, message: str) -> None:
    connection.execute("""
        UPDATE cost_sources SET status = 'error', last_error = %s, updated_at = NOW()
        WHERE connection_id = %s
    """, (message[:2000], connection_id))
    connection.commit()


def collection_start(connection, connection_id: str, end: date) -> date:
    has_history = connection.execute(
        "SELECT 1 FROM cost_records WHERE connection_id = %s LIMIT 1",
        (connection_id,),
    ).fetchone()
    return end - timedelta(days=7 if has_history else 180)


def overview(connection, *, owner_id: str, days: int, provider: str) -> dict[str, Any]:
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=days)
    previous_start = start - timedelta(days=days)
    provider_clause = "AND provider = %s" if provider != "all" else ""
    base_values: list[Any] = [owner_id, start, end]
    if provider != "all":
        base_values.append(provider)

    current = connection.execute(f"""
        SELECT currency, COALESCE(sum(amount), 0) AS amount
        FROM cost_records
        WHERE owner_id = %s AND level = 'service'
          AND usage_date >= %s AND usage_date < %s {provider_clause}
        GROUP BY currency ORDER BY currency
    """, base_values).fetchall()
    previous_values: list[Any] = [owner_id, previous_start, start]
    if provider != "all":
        previous_values.append(provider)
    previous = connection.execute(f"""
        SELECT currency, COALESCE(sum(amount), 0) AS amount
        FROM cost_records
        WHERE owner_id = %s AND level = 'service'
          AND usage_date >= %s AND usage_date < %s {provider_clause}
        GROUP BY currency
    """, previous_values).fetchall()
    previous_by_currency = {row["currency"]: row["amount"] for row in previous}
    totals = []
    for row in current:
        prior = previous_by_currency.get(row["currency"], Decimal(0))
        change = ((row["amount"] - prior) / prior * 100) if prior else None
        totals.append({
            "currency": row["currency"],
            "amount": float(row["amount"]),
            "previous_amount": float(prior),
            "change_percent": float(change) if change is not None else None,
        })

    def grouped(column: str, limit: int | None = None) -> list[dict[str, Any]]:
        limit_sql = f"LIMIT {int(limit)}" if limit else ""
        rows = connection.execute(f"""
            SELECT {column} AS name, currency, sum(amount) AS amount
            FROM cost_records
            WHERE owner_id = %s AND level = 'service'
              AND usage_date >= %s AND usage_date < %s {provider_clause}
            GROUP BY {column}, currency ORDER BY amount DESC {limit_sql}
        """, base_values).fetchall()
        return [{**row, "amount": float(row["amount"])} for row in rows]

    trend_rows = connection.execute(f"""
        SELECT usage_date AS date, currency, sum(amount) AS amount
        FROM cost_records
        WHERE owner_id = %s AND level = 'service'
          AND usage_date >= %s AND usage_date < %s {provider_clause}
        GROUP BY usage_date, currency ORDER BY usage_date, currency
    """, base_values).fetchall()
    trend_index = {
        (row["date"], row["currency"]): float(row["amount"])
        for row in trend_rows
    }
    currencies = [item["currency"] for item in totals]
    trend = []
    for offset in range(days):
        current_day = start + timedelta(days=offset)
        for currency in currencies:
            trend.append({
                "date": current_day,
                "currency": currency,
                "amount": trend_index.get((current_day, currency), 0.0),
            })
    resource_rows = connection.execute(f"""
        WITH aggregated AS (
          SELECT r.owner_id, r.connection_id, r.provider, r.service, r.category,
                 r.currency, r.resource_id, max(r.resource_name) AS billed_name,
                 max(r.region) AS region, sum(r.amount) AS amount
          FROM cost_records r
          WHERE r.owner_id = %s AND r.level = 'resource'
            AND r.usage_date >= %s AND r.usage_date < %s
            {"AND r.provider = %s" if provider != "all" else ""}
          GROUP BY r.owner_id, r.connection_id, r.provider, r.service,
                   r.category, r.currency, r.resource_id
        )
        SELECT a.provider, a.service, a.category, a.currency, a.resource_id,
               COALESCE(
                 (SELECT i.name FROM inventory_resources i
                  WHERE i.owner_id = a.owner_id AND i.connection_id = a.connection_id
                    AND i.resource_id = a.resource_id AND i.name IS NOT NULL
                  ORDER BY i.id LIMIT 1),
                 a.billed_name, a.resource_id
               ) AS resource_name,
               a.region, a.amount
        FROM aggregated a
        ORDER BY amount DESC LIMIT 25
    """, base_values).fetchall()
    service_count = connection.execute(f"""
        SELECT count(DISTINCT (service, currency)) AS count
        FROM cost_records
        WHERE owner_id = %s AND level = 'service'
          AND usage_date >= %s AND usage_date < %s {provider_clause}
    """, base_values).fetchone()["count"]
    estimated = connection.execute(f"""
        SELECT COALESCE(bool_or(estimated), FALSE) AS estimated
        FROM cost_records
        WHERE owner_id = %s AND level = 'service'
          AND usage_date >= %s AND usage_date < %s {provider_clause}
    """, base_values).fetchone()["estimated"]
    sources = list_sources(connection, owner_id)
    return {
        "period": {"start": start, "end": end - timedelta(days=1), "days": days},
        "totals": totals,
        "daily_average": [
            {"currency": item["currency"], "amount": item["amount"] / days}
            for item in totals
        ],
        "by_provider": grouped("provider"),
        "by_category": grouped("category"),
        "top_services": grouped("service", 12),
        "trend": trend,
        "top_resources": [{**row, "amount": float(row["amount"])} for row in resource_rows],
        "service_count": service_count,
        "estimated": estimated,
        "sources": sources,
    }
