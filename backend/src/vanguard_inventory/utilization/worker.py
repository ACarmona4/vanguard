"""Independent metrics polling; telemetry failures never disable cloud inventory."""

import asyncio
import logging
import os
from datetime import datetime, timezone
from threading import Lock

import psycopg
from psycopg.rows import dict_row

from ..accounts import connections as cloud_connections
from ..accounts.credentials import decrypt_credentials
from .catalog import metrics_for
from .cloud import aws_samples, gcp_samples
from .otlp import export

logger = logging.getLogger(__name__)


class MetricsWorker:
    def __init__(self):
        self._lock = Lock()
        self._state = {"status": "pending", "last_finished_at": None, "errors": [], "owners": {}}

    def snapshot(self, owner_id: str | None = None):
        with self._lock:
            if owner_id is not None:
                return dict(self._state["owners"].get(owner_id, {
                    "status": self._state["status"], "last_finished_at": None, "errors": []
                }))
            return {key: value for key, value in self._state.items() if key != "owners"}

    def _set(self, **values):
        with self._lock:
            self._state.update(values)

    def collect(self):
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5) as connection:
            configured = [cloud_connections.get_connection(connection, str(item["id"]), include_credentials=True)
                          for item in cloud_connections.list_connections(connection, enabled_only=True)
                          if item["owner_id"] is not None]
            resources = connection.execute("SELECT * FROM inventory_resources").fetchall()
        now = datetime.now(timezone.utc)
        errors = []
        owner_states = {}
        for item in configured:
            matching = [r for r in resources if r["provider"] == item["provider"]
                        and r["owner_id"] == item["owner_id"]
                        and r["connection_id"] == item["id"] and metrics_for(r)]
            if not matching:
                continue
            try:
                credentials = decrypt_credentials(item["encrypted_credentials"])
                reader = aws_samples if item["provider"] == "aws" else gcp_samples
                samples, failures = reader(matching, credentials, now)
                errors.extend(failures)
                owner = owner_states.setdefault(str(item["owner_id"]), [])
                owner.extend(failures)
                export(samples, os.getenv("VANGUARD_OTLP_METRICS_ENDPOINT", "http://127.0.0.1:4318/v1/metrics"))
            except Exception as exc:
                message = f"{item['name']}: {type(exc).__name__}; check permissions, connection, and Collector"
                errors.append(message)
                owner_states.setdefault(str(item["owner_id"]), []).append(message)
        finished = datetime.now(timezone.utc).isoformat()
        owners = {
            owner_id: {
                "status": "partial" if owner_errors else "success",
                "errors": owner_errors[:30],
                "last_finished_at": finished,
            }
            for owner_id, owner_errors in owner_states.items()
        }
        self._set(
            status="partial" if errors else "success",
            errors=errors[:30],
            owners=owners,
            last_finished_at=finished,
        )

    async def run(self):
        enabled = os.getenv("VANGUARD_METRICS_ENABLED", "true").lower() not in {"false", "0", "off", "no"}
        if not enabled:
            self._set(status="disabled")
            return
        try:
            interval = max(30, int(os.getenv("VANGUARD_METRICS_INTERVAL_SECONDS", "60")))
        except ValueError:
            interval = 60
        while True:
            try:
                await asyncio.to_thread(self.collect)
            except Exception as exc:
                logger.warning("Metrics collection failed: %s", type(exc).__name__)
                self._set(status="error", errors=["Metrics could not be collected; check the database and configuration."])
            await asyncio.sleep(interval)


metrics_worker = MetricsWorker()
