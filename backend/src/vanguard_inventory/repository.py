"""Read-only inventory queries. All filter values are bound parameters."""

from psycopg import Connection, sql

from .schemas import InventorySummary, ResourceFilters, ResourcePage, ResourceQuery


def _where(filters: ResourceFilters) -> tuple[sql.Composed, dict]:
    values = {
        field: getattr(filters, field)
        for field in ResourceFilters.model_fields
        if getattr(filters, field) is not None
        and not (field == "provider" and filters.provider == "all")
    }
    clauses = [
        sql.SQL("{} = {}").format(sql.Identifier(field), sql.Placeholder(field))
        for field in values
    ]
    return (sql.SQL(" AND ").join(clauses) if clauses else sql.SQL("TRUE")), values


def list_resources(connection: Connection, query: ResourceQuery) -> ResourcePage:
    where, values = _where(query)
    total = connection.execute(
        sql.SQL("SELECT count(*) AS total FROM inventory_resources WHERE {}").format(where),
        values,
    ).fetchone()["total"]
    rows = connection.execute(
        sql.SQL("""
            SELECT id, provider, resource_type, resource_id, name, scope_id,
                   region, zone, status, attributes, first_seen_at, last_seen_at
            FROM inventory_resources WHERE {}
            ORDER BY resource_type, region, resource_id, id
            LIMIT %(limit)s OFFSET %(offset)s
        """).format(where),
        {**values, "limit": query.limit, "offset": query.offset},
    ).fetchall()
    return ResourcePage(items=rows, total=total, limit=query.limit, offset=query.offset)


def summarize_resources(connection: Connection, filters: ResourceFilters) -> InventorySummary:
    where, values = _where(filters)
    # One aggregation keeps totals and all three breakdowns consistent.
    rows = connection.execute(
        sql.SQL("""
            SELECT resource_type, region, scope_id, count(*) AS count
            FROM inventory_resources WHERE {}
            GROUP BY resource_type, region, scope_id
        """).format(where), values,
    ).fetchall()
    summary = {"total": sum(row["count"] for row in rows)}
    for key, column in (
        ("by_type", "resource_type"), ("by_region", "region"), ("by_account", "scope_id"),
    ):
        counts = {}
        for row in rows:
            value = row[column]
            counts[value] = counts.get(value, 0) + row["count"]
        summary[key] = [
            {"value": value, "count": count}
            for value, count in sorted(counts.items(), key=lambda item: (item[0] is None, item[0]))
        ]
    return InventorySummary(**summary)
