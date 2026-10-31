"""Organization-scoped opportunities and append-only application audit events."""

from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb

from ..inventory.taxonomy import resource_types_for
from .economics import suggested_impact
from .priority import assess
from .schemas import StoredFinding

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS recommendation_evaluations (
    organization_id UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    evaluated_at TIMESTAMPTZ NOT NULL,
    report JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendation_findings (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    detection_key TEXT NOT NULL,
    finding JSONB NOT NULL,
    workflow_status TEXT NOT NULL DEFAULT 'detected'
        CHECK (workflow_status IN ('detected', 'approved', 'executed', 'rejected')),
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    first_detected_at TIMESTAMPTZ NOT NULL,
    last_detected_at TIMESTAMPTZ NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    business_relevance TEXT NOT NULL DEFAULT 'medium' CHECK (business_relevance IN ('low', 'medium', 'high')),
    economic_impact TEXT NOT NULL DEFAULT 'unknown' CHECK (economic_impact IN ('unknown', 'low', 'medium', 'high')),
    priority TEXT NOT NULL CHECK (priority IN ('critical', 'high', 'medium', 'low')),
    priority_score INTEGER NOT NULL CHECK (priority_score BETWEEN 0 AND 100),
    priority_reason TEXT NOT NULL,
    UNIQUE (organization_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS recommendation_current_key_idx
    ON recommendation_findings (organization_id, detection_key) WHERE is_current;
CREATE INDEX IF NOT EXISTS recommendation_backlog_idx
    ON recommendation_findings (organization_id, workflow_status, priority_score DESC, first_detected_at, id);
CREATE INDEX IF NOT EXISTS recommendation_cloud_idx
    ON recommendation_findings (organization_id, (finding->>'provider'));
CREATE TABLE IF NOT EXISTS recommendation_events (
    id BIGSERIAL PRIMARY KEY,
    organization_id UUID NOT NULL,
    finding_id UUID NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN
        ('detected', 'condition_cleared', 'prioritized', 'approved', 'executed', 'rejected')),
    actor_id UUID REFERENCES app_users(id) ON DELETE SET NULL,
    actor_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    note TEXT NOT NULL,
    snapshot JSONB NOT NULL,
    FOREIGN KEY (organization_id, finding_id)
        REFERENCES recommendation_findings(organization_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS recommendation_event_history_idx
    ON recommendation_events (organization_id, finding_id, id);
"""


class Conflict(ValueError):
    pass


def ensure_schema(connection):
    connection.execute(SCHEMA_SQL)


def public_finding(row):
    return {
        **row["finding"],
        **{
            key: value
            for key, value in row.items()
            if key not in {"organization_id", "finding"}
        },
    }


def _event(connection, row, event_type, note, actor=None):
    connection.execute(
        """INSERT INTO recommendation_events
        (organization_id, finding_id, event_type, actor_id, actor_name, note, snapshot)
        VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (
            row["organization_id"],
            row["id"],
            event_type,
            actor["id"] if actor else None,
            actor["full_name"] if actor else "Recommendation engine",
            note,
            Jsonb(
                StoredFinding.model_validate(public_finding(row)).model_dump(
                    mode="json"
                )
            ),
        ),
    )


def save_evaluation(connection, organization_id, report):
    # Serialize writers across processes. A late older analysis cannot overwrite newer evidence.
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 13))",
        (str(organization_id),),
    )
    stored = connection.execute(
        """INSERT INTO recommendation_evaluations (organization_id, evaluated_at, report)
        VALUES (%s, %s, %s) ON CONFLICT (organization_id) DO UPDATE
        SET evaluated_at = EXCLUDED.evaluated_at, report = EXCLUDED.report
        WHERE recommendation_evaluations.evaluated_at < EXCLUDED.evaluated_at
        RETURNING organization_id""",
        (
            organization_id,
            report["generated_at"],
            Jsonb(
                {
                    key: value
                    for key, value in report.items()
                    if key not in {"items", "checked_keys", "generated_at"}
                }
            ),
        ),
    ).fetchone()
    if not stored:
        return
    existing = {
        row["detection_key"]: row
        for row in connection.execute(
            "SELECT * FROM recommendation_findings WHERE organization_id = %s AND is_current ORDER BY id FOR UPDATE",
            (organization_id,),
        ).fetchall()
    }
    # A person's priority decision always wins over the impact suggested from billing.
    assessed_by_people = {
        row["finding_id"]
        for row in connection.execute(
            "SELECT DISTINCT finding_id FROM recommendation_events "
            "WHERE organization_id = %s AND event_type = 'prioritized'",
            (organization_id,),
        ).fetchall()
    }
    seen = set()
    for finding in report["items"]:
        key = finding["id"]
        seen.add(key)
        current = existing.get(key)
        suggested = suggested_impact(finding.get("cost")) or "unknown"
        if current and current["id"] in assessed_by_people:
            impact = current["economic_impact"]
        else:
            impact = suggested
        ranking = assess(finding, current["business_relevance"] if current else "medium", impact)
        if current:
            connection.execute(
                """UPDATE recommendation_findings SET finding = %s, last_detected_at = %s,
                economic_impact = %s, priority = %s, priority_score = %s, priority_reason = %s,
                version = version + CASE WHEN finding IS DISTINCT FROM %s
                    OR economic_impact IS DISTINCT FROM %s THEN 1 ELSE 0 END
                WHERE organization_id = %s AND id = %s""",
                (
                    Jsonb(finding),
                    report["generated_at"],
                    impact,
                    ranking["priority"],
                    ranking["priority_score"],
                    ranking["priority_reason"],
                    Jsonb(finding),
                    impact,
                    organization_id,
                    current["id"],
                ),
            )
        else:
            row = connection.execute(
                """INSERT INTO recommendation_findings
                (id, organization_id, detection_key, finding, first_detected_at, last_detected_at,
                 economic_impact, priority, priority_score, priority_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *""",
                (
                    uuid4(),
                    organization_id,
                    key,
                    Jsonb(finding),
                    report["generated_at"],
                    report["generated_at"],
                    impact,
                    ranking["priority"],
                    ranking["priority_score"],
                    ranking["priority_reason"],
                ),
            ).fetchone()
            _event(
                connection,
                row,
                "detected",
                "Recommendation created.",
            )
    # Missing telemetry is never proof that a previously detected condition has cleared.
    for key in set(report["checked_keys"]) - seen:
        if key in existing:
            row = connection.execute(
                """UPDATE recommendation_findings SET is_current = FALSE, version = version + 1
                WHERE organization_id = %s AND id = %s RETURNING *""",
                (organization_id, existing[key]["id"]),
            ).fetchone()
            _event(
                connection,
                row,
                "condition_cleared",
                "The condition was no longer detected in the latest check.",
            )
    # A resource removed from the inventory (deleted, or its connection replaced) is never
    # re-checked, so its findings would otherwise stay current forever.
    for row in connection.execute(
        """UPDATE recommendation_findings AS f SET is_current = FALSE, version = version + 1
        WHERE f.organization_id = %s AND f.is_current AND NOT f.detection_key = ANY(%s)
          AND NOT EXISTS (
            SELECT 1 FROM inventory_resources AS r
            WHERE r.owner_id = f.organization_id AND r.id = (f.finding->>'resource_id')::bigint)
        RETURNING *""",
        (organization_id, list(seen)),
    ).fetchall():
        _event(connection, row, "condition_cleared", "The resource is no longer in the inventory.")


def _where(query, organization_id, *, backlog=True):
    clauses = [sql.SQL("organization_id = %(organization_id)s")]
    values = {"organization_id": organization_id}
    for field in ("provider", "resource_type", "region", "scope_id", "status"):
        value = getattr(query, field)
        if value is None or field == "provider" and value == "all":
            continue
        json_key = "resource_status" if field == "status" else field
        grouped = resource_types_for(value) if field == "resource_type" else None
        clauses.append(
            sql.SQL("finding->>{} = ANY(%({})s)").format(
                sql.Literal(json_key), sql.SQL(field)
            )
        )
        values[field] = list(grouped) if grouped else [value]
    if backlog:
        for field in ("kind", "severity", "category"):
            if getattr(query, field):
                clauses.append(
                    sql.SQL("finding->>{} = %({})s").format(
                        sql.Literal(field), sql.SQL(field)
                    )
                )
                values[field] = getattr(query, field)
        if query.priority:
            clauses.append(sql.SQL("priority = %(priority)s"))
            values["priority"] = query.priority
        if query.workflow_status == "open":
            clauses.append(sql.SQL("workflow_status IN ('detected', 'approved')"))
        elif query.workflow_status != "all":
            clauses.append(sql.SQL("workflow_status = %(workflow_status)s"))
            values["workflow_status"] = query.workflow_status
        if query.section:
            clauses.append(sql.SQL(SECTIONS[query.section]))
        if query.detection != "all":
            clauses.append(sql.SQL("is_current" if query.detection == "current" else "NOT is_current"))
    return sql.SQL(" AND ").join(clauses), values


SECTIONS = {
    "savings": "finding->>'category' = 'cost'",
    "alerts": "finding->>'kind' = 'alert'",
    "changes": "finding->>'kind' = 'recommendation' AND finding->>'category' <> 'cost'",
    "monitoring": "finding->>'kind' = 'suggestion'",
}


ORDER_BY = {
    "priority": "CASE priority WHEN 'critical' THEN 0 ELSE 1 END, priority_score DESC, first_detected_at, id",
    "economic_impact": "CASE economic_impact WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END DESC, priority_score DESC, first_detected_at, id",
    "business_relevance": "CASE business_relevance WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC, priority_score DESC, first_detected_at, id",
    "newest": "first_detected_at DESC, id",
}


def list_findings(connection, organization_id, query):
    where, values = _where(query, organization_id)
    total = connection.execute(
        sql.SQL(
            "SELECT count(*) AS total FROM recommendation_findings WHERE {}"
        ).format(where),
        values,
    ).fetchone()["total"]
    rows = connection.execute(
        sql.SQL(
            "SELECT * FROM recommendation_findings WHERE {} ORDER BY {} LIMIT %(limit)s OFFSET %(offset)s"
        ).format(where, sql.SQL(ORDER_BY[query.order_by])),
        {**values, "limit": query.limit, "offset": query.offset},
    ).fetchall()
    return [public_finding(row) for row in rows], total


def open_summary(connection, organization_id, query):
    where, values = _where(query, organization_id, backlog=False)
    row = connection.execute(
        sql.SQL("""SELECT
        count(DISTINCT finding->>'resource_id') FILTER (WHERE is_current) AS affected_resources,
        count(*) FILTER (WHERE is_current AND finding->>'category' = 'cost') AS cost_opportunities,
        count(*) FILTER (WHERE is_current AND finding->>'kind' = 'alert') AS alerts,
        count(*) FILTER (WHERE is_current AND finding->>'kind' = 'recommendation') AS recommendations,
        count(*) FILTER (WHERE is_current AND finding->>'kind' = 'suggestion') AS suggestions,
        count(*) FILTER (WHERE is_current AND finding->>'severity' = 'critical') AS critical,
        count(*) FILTER (WHERE is_current AND finding->>'severity' = 'warning') AS warning,
        count(*) FILTER (WHERE is_current AND finding->>'severity' = 'info') AS info,
        count(*) FILTER (WHERE NOT is_current) AS cleared
        FROM recommendation_findings WHERE {} AND workflow_status IN ('detected', 'approved')""").format(
            where
        ),
        values,
    ).fetchone()
    return row


def get_finding(connection, organization_id, finding_id, *, lock=False):
    row = connection.execute(
        "SELECT * FROM recommendation_findings WHERE organization_id = %s AND id = %s"
        + (" FOR UPDATE" if lock else ""),
        (organization_id, finding_id),
    ).fetchone()
    return row


def history(connection, organization_id, finding_id, limit, offset):
    total = connection.execute(
        "SELECT count(*) AS total FROM recommendation_events WHERE organization_id = %s AND finding_id = %s",
        (organization_id, finding_id),
    ).fetchone()["total"]
    rows = connection.execute(
        "SELECT id, event_type, actor_id, actor_name, created_at, note, snapshot FROM recommendation_events "
        "WHERE organization_id = %s AND finding_id = %s ORDER BY id LIMIT %s OFFSET %s",
        (organization_id, finding_id, limit, offset),
    ).fetchall()
    return rows, total


def change(
    connection, organization_id, finding_id, payload, actor, *, prioritize=False
):
    row = get_finding(connection, organization_id, finding_id, lock=True)
    if not row:
        return None
    if row["version"] != payload.version:
        raise Conflict(
            "This recommendation has changed. Refresh the details and try again."
        )
    if row["workflow_status"] in {"executed", "rejected"}:
        raise Conflict(
            "This recommendation is closed."
        )
    if prioritize:
        ranking = assess(
            row["finding"], payload.business_relevance, payload.economic_impact
        )
        row = connection.execute(
            """UPDATE recommendation_findings SET business_relevance = %s, economic_impact = %s,
            priority = %s, priority_score = %s, priority_reason = %s, version = version + 1
            WHERE organization_id = %s AND id = %s RETURNING *""",
            (
                payload.business_relevance,
                payload.economic_impact,
                ranking["priority"],
                ranking["priority_score"],
                ranking["priority_reason"],
                organization_id,
                finding_id,
            ),
        ).fetchone()
        event_type = "prioritized"
    else:
        expected = {
            "approve": "detected",
            "execute": "approved",
            "reject": row["workflow_status"],
        }[payload.action]
        if row["workflow_status"] != expected:
            raise Conflict(
                "This action is not available in the current status."
            )
        target = {"approve": "approved", "execute": "executed", "reject": "rejected"}[
            payload.action
        ]
        row = connection.execute(
            "UPDATE recommendation_findings SET workflow_status = %s, version = version + 1 "
            "WHERE organization_id = %s AND id = %s RETURNING *",
            (target, organization_id, finding_id),
        ).fetchone()
        event_type = target
    _event(connection, row, event_type, payload.note, actor)
    return public_finding(row)
