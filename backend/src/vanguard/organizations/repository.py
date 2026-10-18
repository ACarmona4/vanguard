"""Shared workspaces and role checks. Legacy owner_id columns store organization IDs."""

from uuid import uuid4

ROLES = ("owner", "admin", "member", "viewer")
ADMIN_ROLES = {"owner", "admin"}
OPERATOR_ROLES = {"owner", "admin", "member"}
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS organization_members (
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    role VARCHAR(16) NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (organization_id, user_id)
);
CREATE INDEX IF NOT EXISTS organization_members_user_idx ON organization_members (user_id);
INSERT INTO organizations (id, name)
SELECT id, left(full_name || ' workspace', 120) FROM app_users ON CONFLICT DO NOTHING;
INSERT INTO organization_members (organization_id, user_id, role)
SELECT id, id, 'owner' FROM app_users ON CONFLICT DO NOTHING;
ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE;
UPDATE auth_sessions SET organization_id = user_id WHERE organization_id IS NULL;
DO $$ BEGIN
    ALTER TABLE auth_sessions ADD CONSTRAINT auth_sessions_organization_required
    CHECK (organization_id IS NOT NULL) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
"""


def ensure_tables(connection):
    connection.execute(SCHEMA_SQL)


def ensure_schema(connection):
    # Repoint legacy ownership without changing IDs or historical telemetry labels.
    # Identifiers come exclusively from this fixed list, never from HTTP input.
    for table in (
        "cloud_connections",
        "inventory_resources",
        "cost_sources",
        "cost_records",
        "lab_deployments",
    ):
        connection.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_owner_id_fkey"
        )
        connection.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_owner_fk"
        )
        connection.execute(f"""
            DO $$ BEGIN
                ALTER TABLE {table} ADD CONSTRAINT {table}_organization_fk
                FOREIGN KEY (owner_id) REFERENCES organizations(id) ON DELETE CASCADE NOT VALID;
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS cloud_connections_owner_id_uidx ON cloud_connections (owner_id, id)"
    )
    for table in (
        "inventory_resources",
        "cost_sources",
        "cost_records",
        "lab_deployments",
    ):
        connection.execute(f"""
            DO $$ BEGIN
                ALTER TABLE {table} ADD CONSTRAINT {table}_organization_connection_fk
                FOREIGN KEY (owner_id, connection_id) REFERENCES cloud_connections(owner_id, id)
                ON DELETE CASCADE NOT VALID;
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)


def create_initial_workspace(connection, user):
    connection.execute(
        "INSERT INTO organizations (id, name) VALUES (%s, %s)",
        (user["id"], (user["full_name"] + " workspace")[:120]),
    )
    connection.execute(
        "INSERT INTO organization_members (organization_id, user_id, role) VALUES (%s, %s, 'owner')",
        (user["id"], user["id"]),
    )


def list_organizations(connection, user_id):
    return connection.execute(
        """
        SELECT o.id, o.name, m.role, o.created_at FROM organizations o
        JOIN organization_members m ON m.organization_id = o.id
        WHERE m.user_id = %s ORDER BY o.created_at, o.id
    """,
        (user_id,),
    ).fetchall()


def create_organization(connection, user_id, name):
    organization_id = str(uuid4())
    row = connection.execute(
        "INSERT INTO organizations (id, name) VALUES (%s, %s) RETURNING id, name, created_at",
        (organization_id, name),
    ).fetchone()
    connection.execute(
        "INSERT INTO organization_members (organization_id, user_id, role) VALUES (%s, %s, 'owner')",
        (organization_id, user_id),
    )
    connection.commit()
    return {**row, "role": "owner"}


def switch_organization(connection, user, organization_id, csrf_token):
    result = connection.execute(
        """
        UPDATE auth_sessions s SET organization_id = m.organization_id, csrf_token = %s
        FROM organization_members m
        WHERE s.token_hash = %s AND s.user_id = %s
          AND m.user_id = s.user_id AND m.organization_id = %s
        RETURNING s.token_hash
    """,
        (csrf_token, user["token_hash"], user["id"], organization_id),
    ).fetchone()
    connection.commit()
    return result is not None


def list_members(connection, organization_id):
    return connection.execute(
        """
        SELECT u.id AS user_id, u.full_name, u.email, m.role, m.created_at
        FROM organization_members m JOIN app_users u ON u.id = m.user_id
        WHERE m.organization_id = %s ORDER BY m.created_at, u.id
    """,
        (organization_id,),
    ).fetchall()


def add_member(connection, organization_id, email, role):
    if role not in {"admin", "member", "viewer"}:
        raise ValueError("Ownership is managed separately")
    row = connection.execute(
        "SELECT id FROM app_users WHERE email = %s", (email,)
    ).fetchone()
    if not row:
        return None
    result = connection.execute(
        """
        INSERT INTO organization_members (organization_id, user_id, role) VALUES (%s, %s, %s)
        ON CONFLICT DO NOTHING RETURNING user_id
    """,
        (organization_id, row["id"], role),
    ).fetchone()
    connection.commit()
    return bool(result)


def change_member(connection, organization_id, user_id, role=None):
    if role is not None and role not in {"admin", "member", "viewer"}:
        raise ValueError("Ownership is managed separately")
    # Ownership cannot be removed or transferred through member management.
    if role is None:
        result = connection.execute(
            """
            DELETE FROM organization_members WHERE organization_id = %s AND user_id = %s AND role <> 'owner'
            RETURNING user_id
        """,
            (organization_id, user_id),
        ).fetchone()
    else:
        result = connection.execute(
            """
            UPDATE organization_members SET role = %s
            WHERE organization_id = %s AND user_id = %s AND role <> 'owner' RETURNING user_id
        """,
            (role, organization_id, user_id),
        ).fetchone()
    if result:
        # Revocation/role changes invalidate active sessions in this workspace,
        # including their CSRF tokens, rather than leaving stale permissions.
        connection.execute(
            "DELETE FROM auth_sessions WHERE user_id = %s AND organization_id = %s",
            (user_id, organization_id),
        )
    connection.commit()
    return result is not None
