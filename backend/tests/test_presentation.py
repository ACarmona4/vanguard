"""Acceptance checks for the complete multicloud presentation dataset."""

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from test_organizations_integration import bootstrap
from vanguard.accounts import auth
from vanguard.organizations import repository as organizations
from vanguard.presentation import repository as presentation
from vanguard.recommendations.telemetry import query_history
from vanguard.utilization.service import describe, query_series

TEST_URL = os.getenv("VANGUARD_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_URL, "A disposable PostgreSQL database is required")
class PresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = "presentation_" + uuid4().hex
        with psycopg.connect(TEST_URL, autocommit=True) as db:
            db.execute(f"CREATE SCHEMA {cls.schema}")
        cls.url = make_conninfo(TEST_URL, options=f"-c search_path={cls.schema}")
        cls.env = patch.dict(os.environ, {"DATABASE_URL": cls.url})
        cls.env.start()
        cls.db = psycopg.connect(cls.url, row_factory=dict_row)
        bootstrap(cls.db)
        cls.user = auth.create_user(
            cls.db,
            email="platform@example.test",
            full_name="Platform Operations",
            password="a local acceptance test password",
        )
        spec = importlib.util.spec_from_file_location(
            "presentation_seed",
            Path(__file__).resolve().parents[2] / "scripts/seed_presentation.py",
        )
        cls.seed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.seed)
        cls.result = cls.seed.seed(cls.db)
        cls.org = cls.seed.WORKSPACE

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.env.stop()
        presentation.OWNERS.discard(cls.org)
        with psycopg.connect(TEST_URL, autocommit=True) as db:
            db.execute(f"DROP SCHEMA {cls.schema} CASCADE")

    def test_dataset_is_separate_and_covers_iaas_paas_and_both_clouds(self):
        rows = self.db.execute(
            "SELECT * FROM inventory_resources WHERE owner_id=%s", (self.org,)
        ).fetchall()
        self.assertEqual(len(rows), 39)
        self.assertEqual({r["provider"] for r in rows}, {"aws", "gcp"})
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) AS n FROM inventory_resources WHERE owner_id=%s",
                (self.user["id"],),
            ).fetchone()["n"],
            0,
        )
        with patch(
            "vanguard.utilization.service.urlopen",
            side_effect=AssertionError("No cloud reads"),
        ):
            described = describe(rows, query_series(rows))
        metrics = [metric for row in described for metric in row["metrics"]]
        self.assertTrue(
            any(
                m["key"] == "memory_percent"
                and m["value"] is not None
                and m["value"] > 90
                for m in metrics
            )
        )
        self.assertTrue(
            all(
                m["status"] == "ok"
                for item in described
                if item["name"] != "reporting-archive-01"
                for m in item["metrics"]
            )
        )
        self.assertGreater(len(query_history(rows)), 100)
        for series in query_history(rows):
            self.assertGreaterEqual(len(series["values"]), 168)

    def test_replay_stays_fresh_and_is_scoped_to_organization(self):
        resource = self.db.execute(
            "SELECT * FROM inventory_resources WHERE owner_id=%s AND name='checkout-api-01'",
            (self.org,),
        ).fetchone()
        first = presentation.series([resource], now=1800000000)
        later = presentation.series([resource], now=1800086400)
        self.assertEqual(later[0]["values"][-1][0] - first[0]["values"][-1][0], 86400)
        self.assertEqual(later[0]["values"][-1][1], first[0]["values"][-1][1])
        with self.assertRaises(ValueError):
            presentation.series([resource, {**resource, "owner_id": uuid4()}])
        self.assertEqual(presentation.series([{**resource, "id": -1}]), [])
        self.assertIsNone(presentation.series([{**resource, "attributes": {}}]))

    def test_all_product_endpoints_return_consistent_data(self):
        from vanguard.api import app

        token, csrf, _ = auth.create_session(self.db, str(self.user["id"]))
        session = auth.get_session(self.db, token)
        organizations.switch_organization(self.db, session, self.org, csrf)
        try:
            with TestClient(app, raise_server_exceptions=True) as client:
                client.cookies.set("vanguard_session", token)
                client.headers["X-Organization-ID"] = self.org
                bodies = {}
                for path in [
                    "/api/summary?provider=all",
                    "/api/resources?provider=all&limit=100",
                    "/api/utilization?limit=100",
                    "/api/utilization/summary",
                    "/api/costs?days=30",
                    "/api/recommendations?limit=100",
                    "/api/tickets",
                    "/api/collection-status",
                ]:
                    result = client.get(path)
                    self.assertEqual(result.status_code, 200, (path, result.text[:300]))
                    bodies[path] = result.json()
                self.assertEqual(bodies["/api/summary?provider=all"]["total"], 39)
                costs = bodies["/api/costs?days=30"]
                self.assertEqual(
                    {p["name"] for p in costs["by_provider"]}, {"aws", "gcp"}
                )
                self.assertEqual(costs["display_currency"], "USD")
                self.assertEqual(len(costs["trend"]), 30)
                recs = bodies["/api/recommendations?limit=100"]
                self.assertGreater(recs["summary"]["alerts"], 5)
                self.assertGreater(recs["summary"]["cost_opportunities"], 5)
                self.assertEqual(recs["notices"], [])
                self.assertNotIn("savings_summary", recs)
                self.assertEqual(
                    bodies["/api/tickets"]["counts"],
                    {"open": 2, "in_progress": 2, "closed": 2},
                )
                resource = bodies["/api/utilization?limit=100"]["items"][0]
                history = client.get(f"/api/utilization/{resource['id']}?hours=168")
                self.assertEqual(history.status_code, 200)
                self.assertGreater(len(history.json()["metrics"][0]["points"]), 2000)
        finally:
            auth.delete_session(self.db, token)
