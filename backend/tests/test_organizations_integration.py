"""Optional tests against a disposable PostgreSQL database, never the application DB."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from vanguard.accounts import auth
from vanguard.connections import repository as connections
from vanguard.costs import repository as costs
from vanguard.inventory import database, repository
from vanguard.inventory.models import Resource
from vanguard.inventory.schemas import ResourceQuery
from vanguard.labs import service as deployments
from vanguard.organizations import repository as organizations

TEST_URL = os.getenv("VANGUARD_TEST_DATABASE_URL")


def bootstrap(connection):
    auth.ensure_tables(connection)
    organizations.ensure_tables(connection)
    connections.ensure_table(connection)
    database.ensure_schema(connection)
    deployments.ensure_table(connection)
    costs.ensure_schema(connection)
    organizations.ensure_schema(connection)
    connection.commit()


@unittest.skipUnless(
    TEST_URL, "Set VANGUARD_TEST_DATABASE_URL to a disposable PostgreSQL instance"
)
class OrganizationsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.schema = "audit_" + uuid4().hex
        with psycopg.connect(TEST_URL, autocommit=True) as connection:
            connection.execute(f"CREATE SCHEMA {self.schema}")
        self.url = make_conninfo(TEST_URL, options=f"-c search_path={self.schema}")
        self.connection = psycopg.connect(self.url, row_factory=dict_row)
        bootstrap(self.connection)
        self.alice = auth.create_user(
            self.connection,
            email="alice@example.test",
            full_name="Alice",
            password="correct password alice",
        )
        self.bob = auth.create_user(
            self.connection,
            email="bob@example.test",
            full_name="Bob",
            password="correct password bob",
        )
        self.company = organizations.create_organization(
            self.connection, self.alice["id"], "Company A"
        )
        self.other = organizations.create_organization(
            self.connection, self.bob["id"], "Company B"
        )
        organizations.add_member(
            self.connection, self.company["id"], self.bob["email"], "viewer"
        )
        self.cloud = connections.create_connection(
            self.connection,
            owner_id=self.company["id"],
            provider="aws",
            name="Production",
            scope_id="123",
            regions=["us-east-1"],
            credential_hint="test",
            identity="test",
            encrypted_credentials="test",
        )
        self.other_cloud = connections.create_connection(
            self.connection,
            owner_id=self.other["id"],
            provider="aws",
            name="Private",
            scope_id="123",
            regions=["us-east-1"],
            credential_hint="test",
            identity="test",
            encrypted_credentials="test",
        )
        sample = Resource(
            provider="aws",
            resource_type="ec2_instance",
            resource_id="i-1",
            scope_id="123",
            region="us-east-1",
            status="running",
        )
        database.save_resources(
            self.url,
            [sample],
            owner_id=str(self.company["id"]),
            connection_id=str(self.cloud["id"]),
        )
        database.save_resources(
            self.url,
            [sample],
            owner_id=str(self.other["id"]),
            connection_id=str(self.other_cloud["id"]),
        )
        self.environment = patch.dict(
            os.environ,
            {"DATABASE_URL": self.url, "VANGUARD_ENVIRONMENT": "development"},
        )
        self.environment.start()
        from vanguard.api import app

        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.environment.stop()
        self.connection.close()
        with psycopg.connect(TEST_URL, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA {self.schema} CASCADE")

    def login(self, user, organization=None):
        token, csrf, _ = auth.create_session(self.connection, str(user["id"]))
        self.client.cookies.set("vanguard_session", token)
        self.client.headers["X-Organization-ID"] = str(user["id"])
        self.csrf = csrf
        if organization:
            result = self.client.post(
                f"/api/organizations/{organization['id']}/switch",
                headers={"X-CSRF-Token": csrf},
            )
            self.assertEqual(result.status_code, 200, result.text)
            self.csrf = result.json()["csrf_token"]
            self.client.headers["X-Organization-ID"] = str(organization["id"])
        return token

    def write(self, method, path, **kwargs):
        return self.client.request(
            method, path, headers={"X-CSRF-Token": self.csrf}, **kwargs
        )

    def test_shared_company_is_selected_instead_of_the_users_private_space(self):
        self.login(self.bob, self.company)
        result = self.client.get("/api/resources?provider=all")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["total"], 1)
        self.assertEqual(
            self.client.get("/api/auth/me").json()["user"]["role"], "viewer"
        )
        scoped = repository.list_resources(
            self.connection, ResourceQuery(provider="all"), str(self.company["id"])
        )
        other_id = (
            repository.list_resources(
                self.connection, ResourceQuery(provider="all"), str(self.other["id"])
            )
            .items[0]
            .id
        )
        self.assertNotEqual(scoped.items[0].id, other_id)
        self.assertEqual(
            self.client.get(f"/api/utilization/{other_id}").status_code, 404
        )

    def test_viewer_cannot_write_or_list_members(self):
        self.login(self.bob, self.company)
        self.assertEqual(self.write("POST", "/api/costs/sync").status_code, 403)
        self.assertEqual(self.client.get("/api/organizations/members").status_code, 403)
        self.assertEqual(
            self.write(
                "DELETE", f"/api/cloud-connections/{self.cloud['id']}"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.write(
                "POST",
                "/api/lab-deployments",
                json={"connection_id": str(self.cloud["id"])},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.write(
                "POST",
                "/api/organizations/members",
                json={"email": self.alice["email"], "role": "admin"},
            ).status_code,
            403,
        )

    def test_members_can_sync_but_cannot_manage_credentials(self):
        organizations.change_member(
            self.connection, self.company["id"], self.bob["id"], "member"
        )
        self.login(self.bob, self.company)
        with patch("vanguard.api.cost_worker.collect") as collect:
            result = self.write("POST", "/api/costs/sync")
        self.assertEqual(result.status_code, 202)
        collect.assert_called_once_with(str(self.company["id"]), True)
        self.assertEqual(
            self.write(
                "DELETE", f"/api/cloud-connections/{self.cloud['id']}"
            ).status_code,
            403,
        )

    def test_switch_requires_membership_and_csrf_and_rotates_csrf(self):
        self.login(self.alice)
        self.assertEqual(
            self.write(
                "POST", f"/api/organizations/{self.other['id']}/switch"
            ).status_code,
            404,
        )
        old_csrf = self.csrf
        result = self.write("POST", f"/api/organizations/{self.company['id']}/switch")
        self.assertEqual(result.status_code, 200)
        self.assertNotEqual(old_csrf, result.json()["csrf_token"])
        self.assertEqual(
            self.client.get("/api/resources?provider=all").status_code, 409
        )
        self.client.headers["X-Organization-ID"] = str(self.company["id"])
        self.assertEqual(
            self.write(
                "POST", "/api/organizations", json={"name": "Rejected"}
            ).status_code,
            403,
        )

    def test_membership_removal_revokes_existing_company_sessions(self):
        self.login(self.bob, self.company)
        organizations.change_member(self.connection, self.company["id"], self.bob["id"])
        self.assertEqual(
            self.client.get("/api/resources?provider=all").status_code, 401
        )

    def test_admin_member_management_preserves_owner(self):
        self.login(self.alice, self.company)
        self.assertEqual(self.client.get("/api/organizations/members").status_code, 200)
        self.assertEqual(
            self.write(
                "PUT",
                f"/api/organizations/members/{self.alice['id']}",
                json={"role": "viewer"},
            ).status_code,
            409,
        )
        self.assertFalse(
            organizations.change_member(
                self.connection, self.company["id"], self.alice["id"], "viewer"
            )
        )
        self.assertEqual(
            self.write(
                "PUT",
                f"/api/organizations/members/{self.bob['id']}",
                json={"role": "admin"},
            ).status_code,
            204,
        )
        self.assertEqual(
            self.write(
                "PUT",
                f"/api/organizations/members/{self.bob['id']}",
                json={"role": "owner"},
            ).status_code,
            422,
        )

    def test_database_rejects_cross_company_connection_reference(self):
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.connection.execute(
                """
                INSERT INTO cost_sources (connection_id, owner_id, provider, status)
                VALUES (%s, %s, 'aws', 'ready')
            """,
                (self.other_cloud["id"], self.company["id"]),
            )
        self.connection.rollback()

    def test_recommendations_and_utilization_queries_work_with_shared_company(self):
        self.login(self.alice, self.company)
        with (
            patch(
                "vanguard.recommendations.api.query_series", return_value=[]
            ),
            patch(
                "vanguard.recommendations.api.query_history", return_value=[]
            ),
        ):
            result = self.client.get("/api/recommendations")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["summary"]["resources"], 1)
        with patch("vanguard.utilization.api.query_series", return_value=[]):
            result = self.client.get("/api/utilization?provider=all&limit=1")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["total"], 1)

    def test_api_security_rejects_untrusted_origin_and_large_bodies(self):
        self.assertEqual(
            self.client.post(
                "/api/auth/login",
                headers={"Origin": "https://attacker.example"},
                json={},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/auth/login", content=b"x" * (256 * 1024 + 1)
            ).status_code,
            413,
        )
        result = self.client.post(
            "/api/auth/login",
            json={"email": "invalid", "password": "a-secret-password"},
        )
        self.assertEqual(result.status_code, 422)
        self.assertNotIn("a-secret-password", result.text)
        self.assertEqual(result.headers["cache-control"], "no-store")

    def test_schema_bootstrap_is_repeatable_and_new_organizations_own_connections(self):
        bootstrap(self.connection)
        self.assertIsNotNone(
            connections.get_connection(
                self.connection, self.cloud["id"], owner_id=self.company["id"]
            )
        )
        self.assertIsNone(
            connections.get_connection(
                self.connection, self.cloud["id"], owner_id=self.other["id"]
            )
        )

    def test_legacy_user_owned_data_and_sessions_migrate_without_changing_identity(
        self,
    ):
        legacy_schema = "legacy_" + uuid4().hex
        with psycopg.connect(TEST_URL, autocommit=True) as root:
            root.execute(f"CREATE SCHEMA {legacy_schema}")
        legacy_url = make_conninfo(TEST_URL, options=f"-c search_path={legacy_schema}")
        try:
            with psycopg.connect(legacy_url, row_factory=dict_row) as connection:
                auth.ensure_tables(connection)
                user_id = str(uuid4())
                connection.execute(
                    "INSERT INTO app_users (id, email, full_name, password_hash, password_salt) VALUES (%s, 'legacy@example.test', 'Legacy', 'unused', 'unused')",
                    (user_id,),
                )
                for schema in (
                    connections.SCHEMA_SQL,
                    database.SCHEMA_SQL,
                    deployments.SCHEMA_SQL,
                    costs.SCHEMA_SQL,
                ):
                    connection.execute(
                        schema.replace(
                            "REFERENCES organizations(id)", "REFERENCES app_users(id)"
                        )
                    )
                connection.commit()
                cloud = connections.create_connection(
                    connection,
                    owner_id=user_id,
                    provider="aws",
                    name="Legacy cloud",
                    scope_id="123",
                    regions=["us-east-1"],
                    credential_hint="unchanged",
                    identity="legacy",
                    encrypted_credentials="unchanged-encrypted-value",
                )
                connection.execute(
                    "INSERT INTO auth_sessions (token_hash, user_id, csrf_token, expires_at) VALUES (%s, %s, 'legacy-csrf', NOW() + INTERVAL '1 day')",
                    (auth._token_hash("legacy-session"), user_id),
                )
                connection.commit()
                sample = Resource(
                    provider="aws",
                    resource_type="ec2_instance",
                    resource_id="legacy-instance",
                    scope_id="123",
                    region="us-east-1",
                )
                database.save_resources(
                    legacy_url,
                    [sample],
                    owner_id=user_id,
                    connection_id=str(cloud["id"]),
                )
                bootstrap(connection)
                session = auth.get_session(connection, "legacy-session")
                self.assertEqual(str(session["organization_id"]), user_id)
                self.assertEqual(session["role"], "owner")
                data = repository.list_resources(
                    connection, ResourceQuery(provider="all"), user_id
                )
                self.assertEqual(data.items[0].resource_id, "legacy-instance")
                saved = connections.get_connection(
                    connection, cloud["id"], owner_id=user_id, include_credentials=True
                )
                self.assertEqual(
                    saved["encrypted_credentials"], "unchanged-encrypted-value"
                )
                shared = organizations.create_organization(
                    connection, user_id, "Shared company"
                )
                created = connections.create_connection(
                    connection,
                    owner_id=shared["id"],
                    provider="aws",
                    name="New shared cloud",
                    scope_id="456",
                    regions=["us-east-1"],
                    credential_hint="test",
                    identity="test",
                    encrypted_credentials="test",
                )
                self.assertEqual(created["owner_id"], shared["id"])
        finally:
            with psycopg.connect(TEST_URL, autocommit=True) as root:
                root.execute(f"DROP SCHEMA {legacy_schema} CASCADE")

    def test_login_throttle_is_atomic_and_passwords_still_authenticate(self):
        for _ in range(5):
            self.assertIsNone(
                auth.authenticate(
                    self.connection,
                    self.alice["email"],
                    "incorrect",
                    remote_address="test",
                )
            )
        self.assertIsNone(
            auth.authenticate(
                self.connection,
                self.alice["email"],
                "correct password alice",
                remote_address="test",
            )
        )
        self.assertIsNotNone(
            auth.authenticate(
                self.connection,
                self.alice["email"],
                "correct password alice",
                remote_address="other",
            )
        )

    def test_concurrent_attempts_cannot_bypass_the_database_limit(self):
        def reserve(_index):
            with psycopg.connect(self.url, row_factory=dict_row) as connection:
                return auth.reserve_attempt(
                    connection, "concurrency", "shared-key", limit=5
                )

        with ThreadPoolExecutor(max_workers=8) as executor:
            allowed = list(executor.map(reserve, range(20)))
        self.assertEqual(sum(allowed), 5)

    def test_inventory_rejects_the_wrong_cloud_scope_before_persistence(self):
        invalid = Resource(
            provider="aws",
            resource_type="ec2_instance",
            resource_id="wrong-account",
            scope_id="999",
            region="us-east-1",
        )
        with self.assertRaises(ValueError):
            database.save_resources(
                self.url,
                [invalid],
                owner_id=str(self.company["id"]),
                connection_id=str(self.cloud["id"]),
            )
        self.assertEqual(
            repository.list_resources(
                self.connection, ResourceQuery(provider="all"), str(self.company["id"])
            ).total,
            1,
        )
