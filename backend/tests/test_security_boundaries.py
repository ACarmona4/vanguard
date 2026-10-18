import json
import os
import unittest
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from vanguard.connections.credentials import (
    CredentialValidationError,
    decrypt_credentials,
    encrypt_credentials,
    gcp_credentials,
)
from vanguard.core.security import validate_production_settings
from vanguard.costs.collectors import _bigquery_rows
from vanguard.inventory.collectors.aws import AWSCollector
from vanguard.inventory.models import CollectionResult
from vanguard.utilization.service import indexed_series, query_series
from vanguard.utilization.worker import MetricsWorker


class SecurityBoundaryTests(unittest.TestCase):
    def test_gcp_credentials_reject_non_google_oauth_endpoints_before_using_sdk(self):
        for token_uri in (
            "http://169.254.169.254/latest/meta-data",
            "https://attacker.example/token",
            "https://oauth2.googleapis.com/token?redirect=bad",
        ):
            with (
                self.subTest(token_uri=token_uri),
                self.assertRaises(CredentialValidationError),
            ):
                gcp_credentials({"type": "service_account", "token_uri": token_uri})
        with self.assertRaises(CredentialValidationError):
            gcp_credentials(
                {"type": "service_account", "universe_domain": "attacker.example"}
            )

    def test_new_credentials_are_bound_to_the_organization(self):
        key = Fernet.generate_key()
        with patch.dict(os.environ, {"VANGUARD_MASTER_KEY": key.decode()}):
            encrypted = encrypt_credentials(
                {"access_key_id": "sample"}, owner_id="company-a"
            )
            self.assertEqual(
                decrypt_credentials(encrypted, owner_id="company-a"),
                {"access_key_id": "sample"},
            )
            with self.assertRaises(ValueError):
                decrypt_credentials(encrypted, owner_id="company-b")
            legacy = (
                Fernet(key)
                .encrypt(json.dumps({"access_key_id": "legacy"}).encode())
                .decode()
            )
            self.assertEqual(
                decrypt_credentials(legacy, owner_id="company-a")["access_key_id"],
                "legacy",
            )

    @patch("vanguard.utilization.service.urlopen")
    def test_metrics_queries_fail_closed_for_missing_or_mixed_organizations(
        self, request
    ):
        for resources in ([{"owner_id": None}], [{"owner_id": "a"}, {"owner_id": "b"}]):
            with self.assertRaises(ValueError):
                query_series(resources)
        request.assert_not_called()

    def test_invalid_metric_values_and_timestamps_do_not_break_the_dashboard(self):
        indexed = indexed_series(
            [
                {
                    "metric": {"__name__": "vanguard_cpu_percent"},
                    "values": [
                        ["invalid", "2"],
                        ["NaN", "2"],
                        ["1", "NaN"],
                        ["Infinity", "5"],
                        ["1", "bad"],
                        ["2", "3"],
                    ],
                }
            ]
        )
        self.assertEqual(list(indexed.values()), [{2.0: 3.0}])

    def test_collection_errors_from_another_company_are_not_inherited(self):
        worker = MetricsWorker()
        worker._set(
            status="partial",
            errors=["company-a failed"],
            owners={"company-a": {"status": "partial", "errors": ["private"]}},
        )
        status = worker.snapshot("company-b")
        self.assertEqual(status["status"], "pending")
        self.assertEqual(status["errors"], [])

    def test_collectors_do_not_publish_provider_exception_secrets(self):
        result = CollectionResult()

        def fail():
            raise RuntimeError("secret-token-from-provider")

        AWSCollector._collect_one(result, "ec2.instances", fail)
        self.assertNotIn("secret-token", result.errors[0])
        self.assertIn("RuntimeError", result.errors[0])

    @patch("vanguard.costs.collectors.time.monotonic", side_effect=[0, 181])
    def test_bigquery_jobs_have_an_overall_deadline(self, _clock):
        session = MagicMock()
        session.post.return_value.json.return_value = {
            "jobComplete": False,
            "jobReference": {"jobId": "job"},
        }
        with self.assertRaises(TimeoutError):
            list(_bigquery_rows(session, "project", "US", "SELECT 1", []))
        session.get.assert_not_called()

    def test_production_rejects_insecure_settings(self):
        base = {
            "VANGUARD_ENVIRONMENT": "production",
            "VANGUARD_MASTER_KEY": Fernet.generate_key().decode(),
            "VANGUARD_SECURE_COOKIES": "true",
            "VANGUARD_PUBLIC_URL": "https://vanguard.example",
            "VANGUARD_EXPOSE_RESET_TOKEN": "false",
        }
        with patch.dict(os.environ, base):
            validate_production_settings()
            for key, value in (
                ("VANGUARD_MASTER_KEY", ""),
                ("VANGUARD_SECURE_COOKIES", "false"),
                ("VANGUARD_EXPOSE_RESET_TOKEN", "true"),
                ("VANGUARD_PUBLIC_URL", "http://vanguard.example"),
            ):
                with (
                    self.subTest(key=key),
                    patch.dict(os.environ, {key: value}),
                    self.assertRaises(RuntimeError),
                ):
                    validate_production_settings()
