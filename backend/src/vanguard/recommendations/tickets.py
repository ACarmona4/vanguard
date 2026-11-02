"""Tenant-scoped implementation tickets with immutable financial snapshots and audit."""

from datetime import date
from types import SimpleNamespace
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS recommendation_tickets (
    id UUID PRIMARY KEY, organization_id UUID NOT NULL,
    finding_id UUID NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','closed')),
    outcome TEXT CHECK (outcome IN ('implemented','cancelled')),
    assignee_id UUID REFERENCES app_users(id) ON DELETE SET NULL,
    due_date DATE, snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ, version INTEGER NOT NULL DEFAULT 1,
    UNIQUE (organization_id, finding_id), UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, finding_id) REFERENCES recommendation_findings(organization_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS recommendation_ticket_events (
    id BIGSERIAL PRIMARY KEY, organization_id UUID NOT NULL, ticket_id UUID NOT NULL,
    event_type TEXT NOT NULL, actor_name TEXT NOT NULL, note TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (organization_id,ticket_id) REFERENCES recommendation_tickets(organization_id,id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS recommendation_tickets_status_idx ON recommendation_tickets(organization_id,status,created_at);
CREATE INDEX IF NOT EXISTS recommendation_ticket_events_history_idx ON recommendation_ticket_events(organization_id,ticket_id,id);
"""

Note = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)
]


class TicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding_id: UUID
    version: int = Field(ge=1)
    note: Note


class TicketUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    action: Literal["approve", "start", "close", "cancel", "assign", "comment"]
    note: Note
    assignee_id: UUID | None = None
    due_date: date | None = None


def event(db, ticket, kind, note, actor):
    db.execute(
        """INSERT INTO recommendation_ticket_events
        (organization_id,ticket_id,event_type,actor_name,note) VALUES (%s,%s,%s,%s,%s)""",
        (ticket["organization_id"], ticket["id"], kind, actor["full_name"], note),
    )


def get(db, org, ticket_id, *, lock=False):
    return db.execute(
        "SELECT * FROM recommendation_tickets WHERE organization_id=%s AND id=%s"
        + (" FOR UPDATE" if lock else ""),
        (org, ticket_id),
    ).fetchone()


def ensure(db, row, actor, *, refresh=False):
    from .repository import public_finding

    ticket = db.execute(
        "SELECT * FROM recommendation_tickets WHERE organization_id=%s AND finding_id=%s FOR UPDATE",
        (row["organization_id"], row["id"]),
    ).fetchone()
    snapshot = public_finding(row)
    from .schemas import StoredFinding

    snapshot = StoredFinding.model_validate(snapshot).model_dump(mode="json")
    if ticket:
        if refresh and ticket["status"] != "closed":
            ticket = db.execute(
                """UPDATE recommendation_tickets SET snapshot=%s, version=version+1, updated_at=now()
                WHERE id=%s AND organization_id=%s RETURNING *""",
                (Jsonb(snapshot), ticket["id"], row["organization_id"]),
            ).fetchone()
            event(
                db,
                ticket,
                "approved",
                "Recommendation approved.",
                actor,
            )
        return ticket
    ticket = db.execute(
        """INSERT INTO recommendation_tickets (id,organization_id,finding_id,assignee_id,snapshot)
        VALUES (%s,%s,%s,%s,%s) RETURNING *""",
        (uuid4(), row["organization_id"], row["id"], actor["id"], Jsonb(snapshot)),
    ).fetchone()
    event(db, ticket, "created", "Ticket created from recommendation.", actor)
    return ticket


def sync_decision(db, row, actor, action, note):
    ticket = ensure(db, row, actor, refresh=action == "approved")
    if action in {"executed", "rejected"} and ticket["status"] != "closed":
        outcome = "implemented" if action == "executed" else "cancelled"
        ticket = db.execute(
            """UPDATE recommendation_tickets SET status='closed',outcome=%s,closed_at=now(),
            updated_at=now(),version=version+1 WHERE organization_id=%s AND id=%s RETURNING *""",
            (outcome, row["organization_id"], ticket["id"]),
        ).fetchone()
        event(db, ticket, outcome, note, actor)
    return ticket


def public(ticket):
    return {
        **{k: v for k, v in ticket.items() if k not in {"organization_id", "snapshot"}},
        "number": "VG-" + str(ticket["id"])[:8].upper(),
        "title": ticket["snapshot"]["title"],
        "resource_name": ticket["snapshot"]["resource_name"],
        "provider": ticket["snapshot"]["provider"],
    }


def create_router(
    database_dependency, write_dependency, user_dependency, operator_dependency
):
    from . import repository

    router = APIRouter(prefix="/api/tickets", tags=["tickets"])

    @router.get("")
    def listing(
        db: database_dependency,
        user: user_dependency,
        status: Literal["all", "open", "in_progress", "closed"] = "all",
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        org = user["organization_id"]
        rows = db.execute(
            """SELECT t.*,u.full_name AS assignee_name FROM recommendation_tickets t
            LEFT JOIN app_users u ON u.id=t.assignee_id
            WHERE t.organization_id=%s AND (%s='all' OR t.status=%s)
            ORDER BY CASE t.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,t.created_at DESC,t.id LIMIT %s OFFSET %s""",
            (org, status, status, limit, offset),
        ).fetchall()
        counts = {
            r["status"]: r["count"]
            for r in db.execute(
                "SELECT status,count(*) AS count FROM recommendation_tickets WHERE organization_id=%s GROUP BY status",
                (org,),
            ).fetchall()
        }
        total = sum(counts.values()) if status == "all" else counts.get(status, 0)
        return {
            "items": [public(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "counts": {k: counts.get(k, 0) for k in ["open", "in_progress", "closed"]},
        }

    @router.get("/assignees")
    def assignees(db: database_dependency, user: user_dependency):
        return db.execute(
            """SELECT u.id,u.full_name FROM organization_members m JOIN app_users u ON u.id=m.user_id
            WHERE m.organization_id=%s AND m.role IN ('owner','admin','member') ORDER BY u.full_name""",
            (user["organization_id"],),
        ).fetchall()

    @router.post("", status_code=201)
    def create(payload: TicketCreate, db: write_dependency, user: operator_dependency):
        row = repository.get_finding(
            db, user["organization_id"], payload.finding_id, lock=True
        )
        if not row:
            raise HTTPException(404, "Recommendation not found")
        if row["version"] != payload.version:
            raise HTTPException(409, "Recommendation changed. Refresh and try again.")
        if row["workflow_status"] in {"executed", "rejected"}:
            raise HTTPException(409, "Recommendation is closed.")
        ticket = ensure(db, row, user)
        event(db, ticket, "comment", payload.note, user)
        db.commit()
        return public(ticket)

    @router.get("/{ticket_id}")
    def detail(
        ticket_id: UUID,
        db: database_dependency,
        user: user_dependency,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        ticket = get(db, user["organization_id"], ticket_id)
        if not ticket:
            raise HTTPException(404, "Ticket not found")
        events = db.execute(
            "SELECT id,event_type,actor_name,note,created_at FROM recommendation_ticket_events WHERE organization_id=%s AND ticket_id=%s ORDER BY id DESC LIMIT 20 OFFSET %s",
            (user["organization_id"], ticket_id, offset),
        ).fetchall()
        count = db.execute(
            "SELECT count(*) AS count FROM recommendation_ticket_events WHERE organization_id=%s AND ticket_id=%s",
            (user["organization_id"], ticket_id),
        ).fetchone()["count"]
        return {
            **public(ticket),
            "snapshot": {k:v for k,v in ticket["snapshot"].items() if k not in {"cost", "savings_scenario"}},
            "events": events,
            "total_events": count,
            "offset": offset,
        }

    @router.put("/{ticket_id}")
    def update(
        ticket_id: UUID,
        payload: TicketUpdate,
        db: write_dependency,
        user: operator_dependency,
    ):
        ticket = get(db, user["organization_id"], ticket_id)
        if not ticket:
            raise HTTPException(404, "Ticket not found")
        # Always lock recommendation first, matching analysis/decision writers.
        row = repository.get_finding(
            db, user["organization_id"], ticket["finding_id"], lock=True
        )
        ticket = get(db, user["organization_id"], ticket_id, lock=True)
        admin = user["role"] in {"owner", "admin"}
        if not admin and ticket["assignee_id"] != user["id"]:
            raise HTTPException(
                403, "Only the assignee or an administrator can update this ticket."
            )
        if ticket["version"] != payload.version:
            raise HTTPException(409, "Ticket changed. Refresh and try again.")
        if ticket["status"] == "closed":
            raise HTTPException(409, "Closed tickets are read-only.")
        if payload.action == "approve":
            if not admin:
                raise HTTPException(
                    403, "An administrator must approve the recommendation."
                )
            try:
                repository.change(
                    db,
                    user["organization_id"],
                    row["id"],
                    SimpleNamespace(
                        version=row["version"], action="approve", note=payload.note
                    ),
                    user,
                )
            except repository.Conflict as exc:
                raise HTTPException(409, str(exc)) from exc
        elif payload.action in {"close", "cancel"}:
            if payload.action == "close" and ticket["status"] != "in_progress":
                raise HTTPException(409, "Start the ticket before closing it.")
            if payload.action == "cancel" and not admin:
                raise HTTPException(403, "An administrator must cancel this ticket.")
            try:
                repository.change(
                    db,
                    user["organization_id"],
                    row["id"],
                    SimpleNamespace(
                        version=row["version"],
                        action="execute" if payload.action == "close" else "reject",
                        note=payload.note,
                    ),
                    user,
                )
            except repository.Conflict as exc:
                raise HTTPException(409, str(exc)) from exc
        elif payload.action == "start":
            if ticket["status"] != "open" or row["workflow_status"] != "approved":
                raise HTTPException(
                    409, "Approve the recommendation before starting its open ticket."
                )
            db.execute(
                "UPDATE recommendation_tickets SET status='in_progress',updated_at=now(),version=version+1 WHERE organization_id=%s AND id=%s",
                (user["organization_id"], ticket_id),
            )
            event(db, ticket, "started", payload.note, user)
        elif payload.action == "assign":
            if not admin:
                raise HTTPException(403, "Only administrators can assign tickets.")
            member = db.execute(
                "SELECT user_id FROM organization_members WHERE organization_id=%s AND user_id=%s AND role IN ('owner','admin','member')",
                (user["organization_id"], payload.assignee_id),
            ).fetchone()
            if not member:
                raise HTTPException(422, "Choose an operator from this organization.")
            db.execute(
                "UPDATE recommendation_tickets SET assignee_id=%s,due_date=%s,updated_at=now(),version=version+1 WHERE organization_id=%s AND id=%s",
                (
                    payload.assignee_id,
                    payload.due_date,
                    user["organization_id"],
                    ticket_id,
                ),
            )
            event(
                db,
                ticket,
                "assigned",
                f"{payload.note} · Assignee {payload.assignee_id} · Due {payload.due_date or 'not set'}",
                user,
            )
        else:
            db.execute(
                "UPDATE recommendation_tickets SET updated_at=now(),version=version+1 WHERE organization_id=%s AND id=%s",
                (user["organization_id"], ticket_id),
            )
            event(db, ticket, "comment", payload.note, user)
        result = get(db, user["organization_id"], ticket_id)
        db.commit()
        return public(result)

    return router
