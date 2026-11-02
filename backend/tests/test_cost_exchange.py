"""USD consolidation: official rate contracts, original billing and tenant boundaries."""

import json
import os
import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from test_organizations_integration import bootstrap
from vanguard.accounts import auth
from vanguard.connections import repository as connections
from vanguard.costs import exchange, repository
from vanguard.costs.collectors import _base_record
from vanguard.costs.exchange_worker import exchange_rate_worker
from vanguard.costs.schemas import CostOverview

TEST_URL = os.getenv("VANGUARD_TEST_DATABASE_URL")


class OfficialRateTests(unittest.TestCase):
    def response(self, rows):
        result = MagicMock()
        result.__enter__.return_value.read.return_value = json.dumps(rows).encode()
        return result

    @patch("vanguard.costs.exchange.urlopen")
    def test_official_rate_has_cop_per_usd_direction_and_holiday_validity(self, request):
        request.return_value = self.response([{"valor": "3182.99", "unidad": "COP", "vigenciadesde": "2026-10-31T00:00:00.000", "vigenciahasta": "2026-11-03T00:00:00.000"}])
        [rate] = exchange.fetch_rates(on_date=date(2026, 11, 2))
        self.assertEqual(rate[:3], (date(2026, 10, 31), date(2026, 11, 3), Decimal("3182.99")))
        self.assertEqual(Decimal("3182.99") / rate[2], Decimal(1))
        self.assertTrue(request.call_args.args[0].full_url.startswith(exchange.API_URL))
        self.assertEqual(request.call_args.kwargs["timeout"], 20)

    @patch("vanguard.costs.exchange.urlopen")
    def test_invalid_and_empty_quotes_are_rejected(self, request):
        for value in ("0", "-2", "NaN", "Infinity", "bad"):
            request.return_value = self.response([{"valor": value, "unidad": "COP", "vigenciadesde": "2026-11-02", "vigenciahasta": "2026-11-03"}])
            with self.subTest(value=value), self.assertRaises(ValueError):
                exchange.fetch_rates(on_date=date(2026, 11, 2))
        request.return_value = self.response([])
        with self.assertRaises(ValueError):
            exchange.fetch_rates()

    @patch("vanguard.costs.exchange.urlopen")
    def test_future_quotes_do_not_revalue_past_bills(self, request):
        request.return_value = self.response([{"valor": "4000", "unidad": "COP", "vigenciadesde": "2026-11-05", "vigenciahasta": "2026-11-06"}])
        with self.assertRaises(ValueError):
            exchange.fetch_rates(on_date=date(2026, 11, 2))


@unittest.skipUnless(TEST_URL, "Set VANGUARD_TEST_DATABASE_URL to a disposable PostgreSQL instance")
class ConsolidatedCostTests(unittest.TestCase):
    def setUp(self):
        self.schema = "usd_" + uuid4().hex
        with psycopg.connect(TEST_URL, autocommit=True) as root:
            root.execute(f"CREATE SCHEMA {self.schema}")
        self.url = make_conninfo(TEST_URL, options=f"-c search_path={self.schema}")
        self.db = psycopg.connect(self.url, row_factory=dict_row)
        bootstrap(self.db)
        self.owner = auth.create_user(self.db, email="usd@example.test", full_name="USD Owner", password="a strong owner password")
        self.other = auth.create_user(self.db, email="other@example.test", full_name="Other", password="a strong other password")
        self.day = date.today()
        self.clouds = {}
        for provider in ("aws", "gcp"):
            cloud = connections.create_connection(self.db, owner_id=self.owner["id"], provider=provider,
                                                  name=provider, scope_id="123" if provider == "aws" else "project-a",
                                                  regions=[], credential_hint="test", identity="test", encrypted_credentials="unused")
            repository.ensure_source(self.db, cloud)
            self.clouds[provider] = cloud
        exchange.save_rates(self.db, [
            (self.day-timedelta(days=20), self.day-timedelta(days=6), Decimal(2000), exchange.SOURCE),
            (self.day-timedelta(days=5), self.day-timedelta(days=2), Decimal(4000), exchange.SOURCE),
            (self.day-timedelta(days=1), self.day+timedelta(days=2), Decimal(8000), exchange.SOURCE),
        ])

    def tearDown(self):
        self.db.close()
        with psycopg.connect(TEST_URL, autocommit=True) as root:
            root.execute(f"DROP SCHEMA {self.schema} CASCADE")

    def save(self, provider, rows):
        cloud = self.clouds[provider]
        source = {"owner_id": self.owner["id"], "connection_id": cloud["id"], "provider": provider, "scope_id": cloud["scope_id"]}
        records = []
        for days, amount, currency in rows:
            usage_day = self.day-timedelta(days=days)
            records.extend([
                _base_record(source, usage_day, "AWS Lambda" if provider == "aws" else "Cloud Storage", amount, currency),
                _base_record(source, usage_day, "AWS Lambda" if provider == "aws" else "Cloud Storage", amount, currency,
                             level="resource", resource_id="arn:aws:lambda:us-east-1:123:function:api" if provider == "aws" else "//storage.googleapis.com/projects/_/buckets/archive"),
            ])
        repository.save_records(self.db, source=source, records=records, start=self.day-timedelta(days=30), end=self.day+timedelta(days=1), detail_available=True)

    def overview(self, **filters):
        result = repository.overview(self.db, owner_id=str(self.owner["id"]), days=7, provider=filters.pop("provider", "all"), **filters)
        CostOverview.model_validate(result)
        return result

    def test_both_clouds_total_trend_and_breakdowns_share_usd_without_double_counting(self):
        self.save("aws", [(0, 3, "USD"), (8, 1, "USD")])
        self.save("gcp", [(3, 4000, "COP"), (0, 8000, "COP"), (8, 4000, "COP")])
        report = self.overview()
        [total] = report["totals"]
        self.assertEqual(total["currency"], "USD")
        self.assertEqual(total["amount"], 5)
        self.assertEqual(total["previous_amount"], 3)
        self.assertAlmostEqual(total["change_percent"], 200/3)
        self.assertEqual({row["name"]: row["amount"] for row in report["by_provider"]}, {"aws": 3, "gcp": 2})
        for key in ("by_category", "top_services", "top_resources", "trend"):
            self.assertTrue(all(row["currency"] == "USD" for row in report[key]), key)
            self.assertAlmostEqual(sum(row["amount"] for row in report[key]), 5)
        self.assertEqual(report["top_resources"][0]["provider"], "aws")
        self.assertEqual(sum(row["amount"] for row in report["conversion"]["original_totals"] if row["currency"] == "COP"), 12000)
        self.assertEqual(report["conversion"]["status"], "ready")

    def test_provider_and_category_filters_apply_to_original_and_usd_amounts(self):
        self.save("aws", [(0, 3, "USD")])
        self.save("gcp", [(0, 8000, "COP")])
        report = self.overview(provider="gcp", infrastructure="Cloud Storage")
        self.assertEqual(report["totals"][0]["amount"], 1)
        self.assertEqual(report["conversion"]["original_totals"], [{"currency": "COP", "amount": 8000}])
        report = self.overview(infrastructure="Serverless")
        self.assertEqual(report["totals"][0]["amount"], 3)
        self.assertEqual(report["conversion"]["status"], "not_needed")

    def test_original_records_and_resource_amounts_are_retained(self):
        self.save("gcp", [(0, 8000, "COP")])
        row = self.overview()["top_resources"][0]
        self.assertEqual((row["amount"], row["original_amount"], row["original_currency"]), (1, 8000, "COP"))
        originals = self.db.execute("SELECT currency, amount FROM cost_records").fetchall()
        self.assertTrue(all(r["currency"] == "COP" and r["amount"] == 8000 for r in originals))
        foreign = repository.overview(self.db, owner_id=str(self.other["id"]), days=7, provider="all")
        self.assertEqual(foreign["totals"], [])
        self.assertEqual(foreign["conversion"]["original_totals"], [])

    def test_no_rate_or_unknown_currency_is_explicit_not_added_as_usd(self):
        self.save("aws", [(0, 3, "USD")])
        self.save("gcp", [(0, 8000, "COP"), (1, 12, "EUR")])
        self.db.execute("DELETE FROM cost_exchange_rates")
        self.db.commit()
        report = self.overview()
        self.assertEqual(report["totals"][0]["amount"], 3)
        self.assertEqual(report["conversion"]["status"], "partial")
        self.assertEqual({row["currency"] for row in report["conversion"]["unconverted"]}, {"COP", "EUR"})
        self.assertIsNone(report["totals"][0]["change_percent"])

    def test_validity_covers_holidays_and_expired_rate_is_disclosed(self):
        self.save("gcp", [(0, 8000, "COP")])
        self.db.execute("DELETE FROM cost_exchange_rates")
        exchange.save_rates(self.db, [(self.day-timedelta(days=3), self.day+timedelta(days=1), Decimal(4000), exchange.SOURCE)])
        self.assertEqual(self.overview()["totals"][0]["amount"], 2)
        self.assertEqual(self.overview()["conversion"]["status"], "ready")
        self.db.execute("UPDATE cost_exchange_rates SET valid_to = %s", (self.day-timedelta(days=1),))
        self.db.commit()
        self.assertEqual(self.overview()["conversion"]["status"], "stale")
        self.db.execute("UPDATE cost_exchange_rates SET valid_from=%s, valid_to=%s", (self.day-timedelta(days=20), self.day-timedelta(days=10)))
        self.db.commit()
        self.assertEqual(self.overview()["conversion"]["status"], "partial")

    def test_missing_previous_conversion_disables_misleading_percentage(self):
        self.save("gcp", [(0, 8000, "COP"), (8, 4000, "COP")])
        self.db.execute("DELETE FROM cost_exchange_rates WHERE valid_from < %s", (self.day-timedelta(days=2),))
        self.db.commit()
        report = self.overview()
        self.assertEqual(report["totals"][0]["amount"], 1)
        self.assertFalse(report["conversion"]["comparison_complete"])
        self.assertIsNone(report["totals"][0]["change_percent"])

    def test_daily_cache_claim_and_failed_refresh_preserve_valid_rates(self):
        self.assertFalse(exchange.claim_refresh(self.db))  # save_rates already populated the daily cache
        before = self.db.execute("SELECT * FROM cost_exchange_rates ORDER BY valid_from").fetchall()
        with patch.dict(os.environ, {"DATABASE_URL": self.url}), patch("vanguard.costs.exchange_worker.fetch_rates", side_effect=TimeoutError("offline")):
            exchange_rate_worker.collect(force=True)
        after = self.db.execute("SELECT * FROM cost_exchange_rates ORDER BY valid_from").fetchall()
        self.assertEqual(before, after)
        self.assertIn("last successful cache", self.overview()["conversion"]["last_error"])
        self.assertFalse(exchange.claim_refresh(self.db))

    def test_a_new_bogota_calendar_day_allows_the_next_quote_refresh(self):
        self.db.execute("UPDATE cost_exchange_sync SET last_success_at=NOW()-INTERVAL '1 day', last_attempt_at=NOW()-INTERVAL '2 hours'")
        self.db.commit()
        self.assertTrue(exchange.claim_refresh(self.db))
        self.assertFalse(exchange.claim_refresh(self.db))
