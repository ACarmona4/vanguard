"""Backlog acceptance checks: durable evidence, ranking, authorized decisions and tenant isolation."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from test_organizations_integration import bootstrap
from test_recommendations import NOW, resource, series
from vanguard.accounts import auth
from vanguard.connections import repository as connections
from vanguard.inventory import database
from vanguard.inventory.models import Resource
from vanguard.organizations import repository as organizations
from vanguard.recommendations import repository
from vanguard.recommendations.engine import confirmed_checks, evaluate
from vanguard.recommendations.priority import assess
from vanguard.recommendations.schemas import Decision, PriorityChange
from vanguard.recommendations.worker import recommendation_worker

TEST_URL = os.getenv("VANGUARD_TEST_DATABASE_URL")


class PriorityAndVerificationTests(unittest.TestCase):
    def test_business_and_economic_impact_change_priority_with_an_explanation(self):
        finding = {"kind": "recommendation", "severity": "info"}
        ordinary = assess(finding)
        important = assess(finding, "high", "high")
        self.assertGreater(important["priority_score"], ordinary["priority_score"])
        self.assertEqual(important["priority"], "high")
        self.assertIn("High cost impact", important["priority_reason"])
        self.assertIn("High business relevance", important["priority_reason"])
        critical = assess({"kind": "alert", "severity": "critical"}, "low", "unknown")
        self.assertEqual(critical["priority"], "critical")
        self.assertEqual(critical["priority_score"], 100)

    def test_absent_rules_need_sufficient_observations_before_they_can_clear(self):
        item = resource()
        key = f"{item['id']}:cpu_percent_high"

        def checked(recent, *, available=True):
            return confirmed_checks(
                [item],
                recent,
                [],
                now=NOW,
                telemetry_available=available,
                history_available=False,
            )

        self.assertNotIn(key, checked([]))
        self.assertNotIn(key, checked([series(item, "cpu_percent", [7])]))
        self.assertNotIn(
            key, checked([series(item, "cpu_percent", [7] * 4)], available=False)
        )
        self.assertIn(key, checked([series(item, "cpu_percent", [7] * 4)]))
        self.assertNotIn(
            f"{item['id']}:cpu_percent_oversized",
            checked([series(item, "cpu_percent", [7] * 4)]),
        )


@unittest.skipUnless(
    TEST_URL, "Set VANGUARD_TEST_DATABASE_URL to a disposable PostgreSQL instance"
)
class RecommendationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.schema = "recommendations_" + uuid4().hex
        with psycopg.connect(TEST_URL, autocommit=True) as connection:
            connection.execute(f"CREATE SCHEMA {self.schema}")
        self.url = make_conninfo(TEST_URL, options=f"-c search_path={self.schema}")
        self.connection = psycopg.connect(self.url, row_factory=dict_row)
        bootstrap(self.connection)
        self.owner = auth.create_user(
            self.connection,
            email="owner@example.test",
            full_name="Owner",
            password="a strong owner password",
        )
        self.member = auth.create_user(
            self.connection,
            email="member@example.test",
            full_name="Member",
            password="a strong member password",
        )
        self.viewer = auth.create_user(
            self.connection,
            email="viewer@example.test",
            full_name="Viewer",
            password="a strong viewer password",
        )
        self.organization_id = self.owner["id"]
        organizations.add_member(
            self.connection, self.organization_id, self.member["email"], "member"
        )
        organizations.add_member(
            self.connection, self.organization_id, self.viewer["email"], "viewer"
        )
        cloud = connections.create_connection(
            self.connection,
            owner_id=self.organization_id,
            provider="gcp",
            name="Test cloud",
            scope_id="project-a",
            regions=["us-central1"],
            credential_hint="test",
            identity="test",
            encrypted_credentials="unused-test-value",
        )
        self.cloud_id = cloud["id"]
        database.save_resources(
            self.url,
            [
                Resource(
                    provider="gcp",
                    resource_type="cloudsql_instance",
                    resource_id="db-1",
                    scope_id="project-a",
                    region="us-central1",
                    name="orders",
                    status="RUNNABLE",
                )
            ],
            owner_id=str(self.organization_id),
            connection_id=str(self.cloud_id),
        )
        item = resource()
        findings, summary = evaluate(
            [item],
            [series(item, "cpu_percent", [7] * 4)],
            [series(item, "cpu_percent", [7] * 168, history=True)],
            now=NOW,
        )
        self.finding = next(
            finding
            for finding in findings
            if finding["rule"] == "cpu_percent_oversized"
        )
        self.at = datetime.now(timezone.utc)
        self.report = {
            "generated_at": self.at,
            "items": [self.finding],
            "checked_keys": [self.finding["id"]],
            "summary": summary,
            "telemetry_status": "available",
            "notices": [],
            "policy": {},
            "sources": [],
            "collection": {"status": "success", "last_finished_at": None, "errors": []},
        }
        self.save(self.report)
        self.finding_id = self.connection.execute(
            "SELECT id FROM recommendation_findings"
        ).fetchone()["id"]
        self.environment = patch.dict(
            os.environ,
            {"DATABASE_URL": self.url, "VANGUARD_ENVIRONMENT": "development"},
        )
        self.environment.start()
        from vanguard.api import app

        self.client = TestClient(app)
        self.login(self.owner)

    def tearDown(self):
        self.client.close()
        self.environment.stop()
        self.connection.close()
        with psycopg.connect(TEST_URL, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA {self.schema} CASCADE")

    def save(self, report):
        repository.save_evaluation(self.connection, self.organization_id, report)
        self.connection.commit()

    def login(self, user, *, own_company=False):
        token, self.csrf, _ = auth.create_session(self.connection, str(user["id"]))
        if not own_company and user["id"] != self.organization_id:
            session = auth.get_session(self.connection, token)
            organizations.switch_organization(
                self.connection, session, self.organization_id, self.csrf
            )
        self.client.cookies.set("vanguard_session", token)
        self.client.headers["X-Organization-ID"] = str(
            user["id"] if own_company else self.organization_id
        )

    def detail(self):
        result = self.client.get(f"/api/recommendations/{self.finding_id}")
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def decide(
        self,
        action,
        *,
        version=None,
        note="Verified change CHG-123 with workload validation",
    ):
        return self.client.post(
            f"/api/recommendations/{self.finding_id}/decisions",
            headers={"X-CSRF-Token": self.csrf},
            json={
                "action": action,
                "note": note,
                "version": version or self.detail()["finding"]["version"],
            },
        )

    def test_persistent_list_detail_and_complete_approval_execution_history(self):
        detail = self.detail()
        for field in (
            "resource_name",
            "cloud_resource_id",
            "description",
            "evidence",
            "actions",
            "priority",
            "workflow_status",
        ):
            self.assertIn(field, detail["finding"])
        self.assertEqual(detail["events"][0]["event_type"], "detected")
        self.assertEqual(self.decide("execute").status_code, 409)
        approved = self.decide("approve")
        self.assertEqual(approved.status_code, 200, approved.text)
        self.login(self.member)
        executed = self.decide("execute")
        self.assertEqual(executed.status_code, 200, executed.text)
        detail = self.detail()
        self.assertEqual(
            [event["event_type"] for event in detail["events"]],
            ["detected", "approved", "executed"],
        )
        self.assertEqual(
            [event["actor_name"] for event in detail["events"]],
            ["Recommendation engine", "Owner", "Member"],
        )
        self.assertEqual(detail["events"][1]["snapshot"]["workflow_status"], "approved")
        self.assertEqual(
            detail["events"][2]["snapshot"]["evidence"], detail["finding"]["evidence"]
        )
        self.assertTrue(
            all(event["created_at"] and event["note"] for event in detail["events"])
        )
        self.assertEqual(self.client.get("/api/recommendations").json()["total"], 0)
        archived = self.client.get(
            "/api/recommendations?workflow_status=executed"
        ).json()
        self.assertEqual(archived["total"], 1)
        self.assertEqual(archived["items"][0]["workflow_status"], "executed")
        self.login(self.owner)
        self.assertEqual(self.decide("reject").status_code, 409)

    def test_role_and_csrf_checks_guard_every_write(self):
        self.login(self.viewer)
        self.assertEqual(self.detail()["finding"]["workflow_status"], "detected")
        self.assertEqual(self.decide("approve").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/api/recommendations/sync", headers={"X-CSRF-Token": self.csrf}
            ).status_code,
            403,
        )
        priority = {
            "version": 1,
            "note": "Important workload",
            "business_relevance": "high",
            "economic_impact": "high",
        }
        self.assertEqual(
            self.client.put(
                f"/api/recommendations/{self.finding_id}/priority",
                headers={"X-CSRF-Token": self.csrf},
                json=priority,
            ).status_code,
            403,
        )
        self.login(self.member)
        self.assertEqual(self.decide("approve").status_code, 403)
        self.assertEqual(self.decide("reject").status_code, 403)
        self.assertEqual(self.decide("execute").status_code, 409)
        self.login(self.owner)
        self.assertEqual(
            self.client.post(
                f"/api/recommendations/{self.finding_id}/decisions",
                json={"action": "approve", "note": "Approved change", "version": 1},
            ).status_code,
            403,
        )
        self.assertEqual(self.decide("approve", note="   ").status_code, 422)
        self.assertEqual(self.detail()["total_events"], 1)

    def test_other_company_cannot_read_change_or_enumerate_a_finding(self):
        self.login(self.member, own_company=True)
        self.assertEqual(
            self.client.get("/api/recommendations?workflow_status=all").json()["total"],
            0,
        )
        self.assertEqual(
            self.client.get(f"/api/recommendations/{self.finding_id}").status_code, 404
        )
        self.assertEqual(self.decide("approve", version=1).status_code, 404)
        response = self.client.put(
            f"/api/recommendations/{self.finding_id}/priority",
            headers={"X-CSRF-Token": self.csrf},
            json={
                "version": 1,
                "note": "Wrong company",
                "business_relevance": "high",
                "economic_impact": "high",
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_prioritization_changes_order_filters_and_keeps_justification(self):
        second = {
            **self.finding,
            "id": "2:cpu_percent_oversized",
            "resource_id": 2,
            "resource_name": "Billing DB",
        }
        self.save(
            {
                **self.report,
                "generated_at": self.at + timedelta(seconds=1),
                "items": [self.finding, second],
            }
        )
        second_id = self.connection.execute(
            "SELECT id FROM recommendation_findings WHERE detection_key = %s",
            (second["id"],),
        ).fetchone()["id"]
        result = self.client.put(
            f"/api/recommendations/{second_id}/priority",
            headers={"X-CSRF-Token": self.csrf},
            json={
                "version": 1,
                "note": "Large provisioned capacity; critical billing workload, review FIN-42",
                "business_relevance": "high",
                "economic_impact": "high",
            },
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["priority"], "high")
        page = self.client.get(
            "/api/recommendations?priority=high&order_by=economic_impact&limit=1"
        ).json()
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["id"], str(second_id))
        events = self.client.get(f"/api/recommendations/{second_id}").json()["events"]
        self.assertEqual(events[-1]["event_type"], "prioritized")
        self.assertIn("FIN-42", events[-1]["note"])
        self.assertEqual(events[-1]["snapshot"]["economic_impact"], "high")
        self.assertEqual(
            self.client.get("/api/recommendations?limit=1&offset=1").json()["total"], 2
        )
        for query in (
            "priority=invalid",
            "workflow_status=invalid",
            "order_by=raw_sql",
            "limit=101",
        ):
            self.assertEqual(
                self.client.get("/api/recommendations?" + query).status_code, 422
            )

    def test_refresh_preserves_decisions_outage_does_not_clear_and_recurrence_has_new_id(
        self,
    ):
        self.assertEqual(self.decide("reject").status_code, 200)
        self.save({**self.report, "generated_at": self.at + timedelta(seconds=1)})
        self.assertEqual(self.detail()["finding"]["workflow_status"], "rejected")
        self.assertEqual(self.detail()["total_events"], 2)
        self.save(
            {
                **self.report,
                "generated_at": self.at + timedelta(seconds=2),
                "items": [],
                "checked_keys": [],
                "telemetry_status": "unavailable",
            }
        )
        self.assertTrue(self.detail()["finding"]["is_current"])
        self.save(
            {**self.report, "generated_at": self.at + timedelta(seconds=3), "items": []}
        )
        self.assertFalse(self.detail()["finding"]["is_current"])
        self.assertEqual(self.detail()["events"][-1]["event_type"], "condition_cleared")
        self.save({**self.report, "generated_at": self.at + timedelta(seconds=4)})
        all_items = self.client.get("/api/recommendations?workflow_status=all").json()[
            "items"
        ]
        self.assertEqual(len(all_items), 2)
        renewed = next(item for item in all_items if item["id"] != str(self.finding_id))
        self.assertEqual(renewed["workflow_status"], "detected")
        self.assertTrue(renewed["is_current"])
        self.assertEqual(self.detail()["finding"]["workflow_status"], "rejected")

    def test_stale_analysis_cannot_overwrite_newer_data(self):
        changed = {**self.finding, "description": "Newer observed condition"}
        self.save(
            {
                **self.report,
                "generated_at": self.at + timedelta(seconds=2),
                "items": [changed],
            }
        )
        self.save(
            {**self.report, "generated_at": self.at + timedelta(seconds=1), "items": []}
        )
        detail = self.detail()
        self.assertEqual(detail["finding"]["description"], "Newer observed condition")
        self.assertTrue(detail["finding"]["is_current"])
        self.assertEqual(
            detail["events"][0]["snapshot"]["description"], self.finding["description"]
        )
        self.assertEqual(detail["total_events"], 1)

    def test_conflicting_actions_are_atomic_and_only_one_can_win(self):
        barrier = Barrier(2)
        version = self.detail()["finding"]["version"]

        def decide(action):
            with psycopg.connect(self.url, row_factory=dict_row) as connection:
                barrier.wait(timeout=5)
                try:
                    repository.change(
                        connection,
                        self.organization_id,
                        self.finding_id,
                        Decision(
                            version=version,
                            note="Concurrent decision audit",
                            action=action,
                        ),
                        self.owner,
                    )
                    connection.commit()
                    return "saved"
                except repository.Conflict:
                    connection.rollback()
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(decide, ["approve", "reject"]))
        self.assertEqual(sorted(outcomes), ["conflict", "saved"])
        self.assertEqual(self.detail()["total_events"], 2)

    def test_snapshot_survives_inventory_deletion_and_history_is_paginated(self):
        # No FK to inventory: discovery cleanup cannot erase the audit record.
        self.connection.execute(
            "DELETE FROM cloud_connections WHERE owner_id = %s AND id = %s",
            (self.organization_id, self.cloud_id),
        )
        self.connection.commit()
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) AS count FROM inventory_resources"
            ).fetchone()["count"],
            0,
        )
        detail = self.detail()
        self.assertEqual(
            detail["finding"]["cloud_resource_id"], self.finding["cloud_resource_id"]
        )
        for index in range(21):
            row = repository.get_finding(
                self.connection, self.organization_id, self.finding_id
            )
            repository.change(
                self.connection,
                self.organization_id,
                self.finding_id,
                PriorityChange(
                    version=row["version"],
                    note=f"Business assessment {index}",
                    business_relevance="high",
                    economic_impact="medium",
                ),
                self.owner,
                prioritize=True,
            )
            self.connection.commit()
        page = self.client.get(
            f"/api/recommendations/{self.finding_id}?limit=20&offset=20"
        ).json()
        self.assertEqual(page["total_events"], 22)
        self.assertEqual(len(page["events"]), 2)
        self.assertEqual(page["offset"], 20)

    def test_background_analysis_saves_without_any_dashboard_request(self):
        self.connection.execute("DELETE FROM recommendation_findings")
        self.connection.execute("DELETE FROM recommendation_evaluations")
        self.connection.commit()
        with (
            patch("vanguard.recommendations.analysis.query_series", return_value=[]),
            patch("vanguard.recommendations.analysis.query_history", return_value=[]),
        ):
            recommendation_worker.collect()
        saved = self.client.get("/api/recommendations").json()
        self.assertGreater(saved["total"], 0)
        self.assertEqual(saved["summary"]["resources"], 1)
        self.assertEqual(saved["analysis_status"], "current")

    def test_more_than_one_page_is_persisted_without_duplicate_detections(self):
        findings = [
            {
                **self.finding,
                "id": f"{index}:cpu_percent_oversized",
                "resource_id": index,
            }
            for index in range(1, 106)
        ]
        report = {
            **self.report,
            "generated_at": self.at + timedelta(seconds=1),
            "items": findings,
        }
        self.save(report)
        self.save({**report, "generated_at": self.at + timedelta(seconds=2)})
        first = self.client.get("/api/recommendations?limit=100").json()
        last = self.client.get("/api/recommendations?limit=100&offset=100").json()
        self.assertEqual(first["total"], 105)
        self.assertEqual(len(first["items"]), 100)
        self.assertEqual(len(last["items"]), 5)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) AS count FROM recommendation_events"
            ).fetchone()["count"],
            105,
        )
