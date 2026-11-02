import os
import unittest

import test_recommendation_workflow as workflow_tests


@unittest.skipUnless(
    os.getenv("VANGUARD_TEST_DATABASE_URL"), "Disposable PostgreSQL required"
)
class TicketIntegrationTests(unittest.TestCase):
    setUp = workflow_tests.RecommendationWorkflowTests.setUp
    tearDown = workflow_tests.RecommendationWorkflowTests.tearDown
    login = workflow_tests.RecommendationWorkflowTests.login
    save = workflow_tests.RecommendationWorkflowTests.save
    detail = workflow_tests.RecommendationWorkflowTests.detail
    decide = workflow_tests.RecommendationWorkflowTests.decide

    def create(self):
        return self.client.post(
            "/api/tickets",
            headers={"X-CSRF-Token": self.csrf},
            json={
                "finding_id": str(self.finding_id),
                "version": self.detail()["finding"]["version"],
                "note": "Plan reviewed with service owner.",
            },
        )

    def ticket(self, id):
        result = self.client.get("/api/tickets/" + id)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def update(self, id, action, **extra):
        return self.client.put(
            "/api/tickets/" + id,
            headers={"X-CSRF-Token": self.csrf},
            json={
                "version": self.ticket(id)["version"],
                "action": action,
                "note": "Validated with owner and workload checks.",
                **extra,
            },
        )

    def test_full_lifecycle_scenario_snapshot_assignment_and_audit(self):
        result = self.create()
        self.assertEqual(result.status_code, 201, result.text)
        id = result.json()["id"]
        self.assertEqual(self.create().json()["id"], id)
        self.assertEqual(self.update(id, "start").status_code, 409)
        self.assertEqual(self.update(id, "approve").status_code, 200)
        self.assertEqual(
            self.update(
                id, "assign", assignee_id=str(self.member["id"]), due_date="2026-11-03"
            ).status_code,
            200,
        )
        self.login(self.member)
        self.assertEqual(self.update(id, "approve").status_code, 403)
        self.assertEqual(self.update(id, "start").status_code, 200)
        self.assertEqual(
            self.client.get("/api/tickets?status=in_progress").json()["total"], 1
        )
        self.assertEqual(self.update(id, "comment").status_code, 200)
        self.assertEqual(self.update(id, "close").status_code, 200)
        self.assertEqual(self.detail()["finding"]["workflow_status"], "executed")
        ticket = self.ticket(id)
        self.assertEqual(
            (ticket["status"], ticket["outcome"]), ("closed", "implemented")
        )
        self.assertEqual(self.update(id, "start").status_code, 409)
        self.assertIn(
            "implemented", [event["event_type"] for event in ticket["events"]]
        )
        self.assertEqual(
            self.client.get("/api/tickets").json()["counts"],
            {"open": 0, "in_progress": 0, "closed": 1},
        )

    def test_existing_recommendation_buttons_keep_ticket_in_sync(self):
        self.assertEqual(self.decide("approve").status_code, 200)
        row = self.client.get("/api/tickets").json()["items"][0]
        self.assertEqual(row["status"], "open")
        self.assertEqual(self.decide("execute").status_code, 200)
        self.assertEqual(self.ticket(row["id"])["outcome"], "implemented")

    def test_scope_roles_csrf_conflicts_and_cancellation(self):
        id = self.create().json()["id"]
        version = self.ticket(id)["version"]
        self.assertEqual(
            self.client.put(
                "/api/tickets/" + id,
                json={"version": version, "action": "comment", "note": "Missing CSRF"},
            ).status_code,
            403,
        )
        self.assertEqual(self.update(id, "comment").status_code, 200)
        self.assertEqual(self.update(id, "comment", version=version).status_code, 409)
        self.assertEqual(
            self.update(id, "assign", assignee_id=str(self.viewer["id"])).status_code,
            422,
        )
        self.login(self.member)
        self.assertEqual(self.update(id, "start").status_code, 403)
        self.login(self.viewer)
        self.assertEqual(self.create().status_code, 403)
        self.login(self.member, own_company=True)
        self.assertEqual(self.client.get("/api/tickets/" + id).status_code, 404)
        self.assertEqual(self.client.get("/api/tickets").json()["total"], 0)
        self.login(self.owner)
        self.assertEqual(self.update(id, "cancel").status_code, 200)
        self.assertEqual(self.detail()["finding"]["workflow_status"], "rejected")
        self.assertEqual(self.ticket(id)["outcome"], "cancelled")
