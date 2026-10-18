"""Optional compatibility check using the deployment's real PromQL engine."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from vanguard.recommendations.telemetry import query_history

PROMTOOL = os.getenv("VANGUARD_PROMTOOL") or shutil.which("promtool")


@unittest.skipUnless(PROMTOOL, "Set VANGUARD_PROMTOOL to run the Prometheus integration check")
class RecommendationPromQLTests(unittest.TestCase):
    def test_history_preserves_metric_identities_and_rejects_sparse_buckets(self):
        resource = {"owner_id": "owner-a", "provider": "aws", "scope_id": "123", "region": "us-east-1",
                    "resource_type": "ec2_instance", "resource_id": "i-1"}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"status":"success","data":{"resultType":"matrix","result":[]}}'
        with patch("vanguard.recommendations.telemetry.urlopen", return_value=response) as request:
            query_history([resource], now=1_800_000_000)
        query = parse_qs(urlparse(request.call_args.args[0]).query)["query"][0]
        labels = ",".join(f'{key}="{value}"' for key, value in sorted(resource.items()))
        cpu = "vanguard_cpu_percent{" + labels + "}"
        network = "vanguard_network_out_bytes_per_second{" + labels + "}"
        foreign = cpu.replace('owner-a', 'owner-b')
        tests = {
            "evaluation_interval": "5m",
            "tests": [
                {"name": "distinct metric identities and owner isolation", "interval": "5m",
                 "input_series": [{"series": cpu, "values": "7+0x12"},
                                  {"series": network, "values": "2097152+0x12"},
                                  {"series": foreign, "values": "99+0x12"}],
                 "promql_expr_test": [{"expr": query, "eval_time": "1h", "exp_samples": [
                     {"labels": cpu, "value": 7}, {"labels": network, "value": 2097152}]}]},
                {"name": "sparse bucket is not evidence of low usage", "interval": "5m",
                 "input_series": [{"series": cpu, "values": "7 _ _ _ _ _ _ _ _ _ _ 7 7"}],
                 "promql_expr_test": [{"expr": query, "eval_time": "1h", "exp_samples": []}]},
            ],
        }
        with tempfile.TemporaryDirectory(prefix="vanguard-promql-") as directory:
            path = Path(directory) / "history.test.json"
            path.write_text(json.dumps(tests))
            result = subprocess.run([PROMTOOL, "test", "rules", str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
