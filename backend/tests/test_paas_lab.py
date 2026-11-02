"""Persistent lab: repeatable archives, safe upgrades, real state ownership and test handlers."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from test_organizations_integration import bootstrap
from vanguard.accounts import auth
from vanguard.connections import repository as connections
from vanguard.labs import service

TEST_URL = os.getenv("VANGUARD_TEST_DATABASE_URL")


class LabModuleTests(unittest.TestCase):
    def test_lambda_archive_is_deterministic_and_contains_the_deployed_handler(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            service.stage_module(Path(first))
            service.stage_module(Path(second))
            self.assertEqual((Path(first)/"lambda.zip").read_bytes(), (Path(second)/"lambda.zip").read_bytes())
            with zipfile.ZipFile(Path(first)/"lambda.zip") as archive:
                self.assertEqual(archive.namelist(), ["handler.py"])
                self.assertEqual(archive.read("handler.py"), (service.MODULE/"lambda_handler.py").read_bytes())

    def test_upgrade_rejects_replacements_deletions_and_invalid_plans(self):
        for actions in (["delete"], ["delete", "create"], ["create", "delete"], ["forget"], []):
            with self.subTest(actions=actions), self.assertRaises(service.LabUpgradeConflict):
                service.verify_upgrade_plan({"planned_values": {}, "resource_changes": [{"change": {"actions": actions}}]})
        with self.assertRaises(service.LabUpgradeConflict):
            service.verify_upgrade_plan({})
        service.verify_upgrade_plan({"planned_values": {}, "resource_changes": [{"change": {"actions": [action]}} for action in ("create", "update", "read", "no-op")]})

    def test_upgrade_pins_only_the_existing_lab_instance_image(self):
        state = {"resources": [{"type": "aws_instance", "name": "lab", "instances": [{"index_key": 0, "attributes": {"ami": "ami-0123456789abcdef0"}}]}]}
        self.assertEqual(service.existing_lab_ami(state), "ami-0123456789abcdef0")
        state["resources"][0]["instances"][0]["attributes"]["ami"] = "invalid -var=unexpected"
        self.assertIsNone(service.existing_lab_ami(state))
        self.assertIsNone(service.existing_lab_ami(None))

    def handler(self):
        spec = importlib.util.spec_from_file_location("lab_handler", service.MODULE/"lambda_handler.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_producer_uses_synthetic_data_with_a_ttl_and_a_controlled_error(self):
        handler = self.handler()
        database, topic = Mock(), Mock()
        with patch.dict(os.environ, {"TABLE_NAME": "lab-table", "TOPIC_ARN": "lab-topic"}), patch.object(handler.boto3, "client", side_effect=[database, topic]), patch("builtins.print"):
            result = handler.producer({}, None)
        item = database.put_item.call_args.kwargs["Item"]
        self.assertEqual(item["id"]["S"], "sent#"+result["id"])
        payload = json.loads(topic.publish.call_args.kwargs["Message"])
        self.assertEqual(payload["source"], "vanguard-paas-lab")
        self.assertEqual(int(item["lab_expires_at"]["N"]), payload["created_at"]+86400)
        with patch.object(handler.boto3, "client") as client, self.assertRaisesRegex(RuntimeError, "controlled"):
            handler.producer({"simulate_error": True}, None)
        client.assert_not_called()

    def test_consumer_returns_only_failed_items_and_preserves_successful_messages(self):
        handler = self.handler()
        database = Mock()
        event = {"Records": [{"messageId": "good", "body": json.dumps({"id": "abc", "source": "vanguard-paas-lab"})},
                             {"messageId": "bad", "body": '{"source":"unrelated"}'}]}
        with patch.dict(os.environ, {"TABLE_NAME": "lab-table"}), patch.object(handler.boto3, "client", return_value=database), patch("builtins.print"):
            result = handler.consumer(event, None)
        self.assertEqual(result, {"batchItemFailures": [{"itemIdentifier": "bad"}]})
        self.assertEqual(database.put_item.call_args.kwargs["Item"]["id"]["S"], "processed#abc")
        database.put_item.assert_called_once()


@unittest.skipUnless(TEST_URL, "Set VANGUARD_TEST_DATABASE_URL to a disposable PostgreSQL instance")
class LabUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.schema = "paas_lab_"+uuid4().hex
        with psycopg.connect(TEST_URL, autocommit=True) as root:
            root.execute(f"CREATE SCHEMA {self.schema}")
        self.url = make_conninfo(TEST_URL, options=f"-c search_path={self.schema}")
        self.db = psycopg.connect(self.url, row_factory=dict_row)
        bootstrap(self.db)
        self.owner = auth.create_user(self.db, email="lab@example.test", full_name="Lab Owner", password="a strong owner password")
        self.cloud = connections.create_connection(self.db, owner_id=self.owner["id"], provider="aws", name="Lab", scope_id="123",
                                                    regions=["us-east-1"], credential_hint="test", identity="test", encrypted_credentials="unused")
        self.lab_id = uuid4()
        self.state = {"version": 4, "resources": [{"type": "aws_instance", "name": "lab", "instances": [{"attributes": {"id": "i-existing"}}]}]}
        self.db.execute("INSERT INTO lab_deployments (id,owner_id,connection_id,provider,region,status,terraform_state) VALUES (%s,%s,%s,'aws','us-east-1','active',%s)",
                        (self.lab_id, self.owner["id"], self.cloud["id"], Jsonb(self.state)))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        with psycopg.connect(TEST_URL, autocommit=True) as root:
            root.execute(f"DROP SCHEMA {self.schema} CASCADE")

    def test_upgrade_is_owner_scoped_and_preserves_identity_and_state(self):
        self.assertIsNone(service.queue_upgrade(self.db, owner_id=str(uuid4()), deployment_id=str(self.lab_id)))
        row = service.queue_upgrade(self.db, owner_id=str(self.owner["id"]), deployment_id=str(self.lab_id))
        self.assertEqual(row["id"], self.lab_id)
        self.assertEqual(row["status"], "queued")
        self.assertIsNone(service.queue_upgrade(self.db, owner_id=str(self.owner["id"]), deployment_id=str(self.lab_id)))
        self.assertEqual(self.db.execute("SELECT terraform_state FROM lab_deployments WHERE id=%s",(self.lab_id,)).fetchone()["terraform_state"], self.state)

    def test_rejected_plan_never_applies_and_retains_state_for_cleanup(self):
        service.queue_upgrade(self.db, owner_id=str(self.owner["id"]), deployment_id=str(self.lab_id))
        plan = {"planned_values": {}, "resource_changes": [{"change": {"actions": ["delete", "create"]}}]}
        def command(args, **_):
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(plan) if args[1] == "show" else "", stderr="")
        with patch("vanguard.labs.service.decrypt_credentials", return_value={"access_key_id": "test", "secret_access_key": "test"}), patch("vanguard.labs.service.subprocess.run", side_effect=command) as execute:
            service.run(self.url, str(self.lab_id), owner_id=str(self.owner["id"]), upgrade=True)
        self.assertFalse(any(call.args[0][1] == "apply" for call in execute.call_args_list))
        row = self.db.execute("SELECT status,last_error,terraform_state FROM lab_deployments WHERE id=%s", (self.lab_id,)).fetchone()
        self.assertEqual(row["status"], "error")
        self.assertIn("replace existing resources", row["last_error"])
        self.assertEqual(row["terraform_state"], self.state)
        self.assertTrue(service.requires_cleanup(self.db, str(self.cloud["id"]), owner_id=str(self.owner["id"])))
