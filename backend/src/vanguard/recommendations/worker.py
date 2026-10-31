"""Persist detections independently of dashboard visits; one failed company cannot stop others."""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg.rows import dict_row

from . import repository
from .analysis import evaluate_inventory

logger = logging.getLogger(__name__)


def collect_workspace(connection, organization_id):
    report = evaluate_inventory(connection, str(organization_id))
    repository.save_evaluation(connection, organization_id, report)
    connection.commit()


class RecommendationWorker:
    def collect(self):
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            return
        with psycopg.connect(
            database_url, row_factory=dict_row, connect_timeout=5
        ) as connection:
            # Workspaces whose connections are all disabled collect nothing new; re-evaluating
            # them would only turn their stale inventory into missing-metric findings.
            organizations = connection.execute(
                "SELECT o.id FROM (SELECT DISTINCT owner_id AS id FROM inventory_resources WHERE owner_id IS NOT NULL "
                "UNION SELECT DISTINCT organization_id AS id FROM recommendation_findings) AS o "
                "WHERE NOT EXISTS (SELECT 1 FROM cloud_connections c WHERE c.owner_id = o.id) "
                "OR EXISTS (SELECT 1 FROM cloud_connections c WHERE c.owner_id = o.id AND c.enabled)"
            ).fetchall()

        def collect_one(organization):
            try:
                with psycopg.connect(
                    database_url, row_factory=dict_row, connect_timeout=5
                ) as connection:
                    connection.execute("SET statement_timeout = '15000ms'")
                    connection.execute("SET lock_timeout = '3000ms'")
                    collect_workspace(connection, organization["id"])
            except Exception as exc:
                logger.warning(
                    "Recommendation analysis failed for organization %s: %s",
                    organization["id"],
                    type(exc).__name__,
                )

        with ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="recommendations"
        ) as executor:
            list(executor.map(collect_one, organizations))

    async def run(self):
        if os.getenv("VANGUARD_RECOMMENDATIONS_ENABLED", "true").lower() in {
            "false",
            "0",
            "off",
            "no",
        }:
            return
        try:
            interval = max(
                60, int(os.getenv("VANGUARD_RECOMMENDATIONS_INTERVAL_SECONDS", "300"))
            )
        except ValueError:
            interval = 300
        while True:
            try:
                await asyncio.to_thread(self.collect)
            except Exception as exc:
                logger.warning("Recommendation worker failed: %s", type(exc).__name__)
            await asyncio.sleep(interval)


recommendation_worker = RecommendationWorker()
