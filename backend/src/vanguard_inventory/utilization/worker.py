"""Independent metrics polling; telemetry failures never disable cloud inventory."""

import asyncio
import logging
import os
from datetime import datetime, timezone
from threading import Lock

import psycopg
from psycopg.rows import dict_row

from .. import cloud_connections
from ..cloud_credentials import decrypt_credentials
from .catalog import metrics_for
from .cloud import aws_samples, gcp_samples
from .otlp import export

logger = logging.getLogger(__name__)


class MetricsWorker:
    def __init__(self):
        self._lock = Lock()
        self._state = {"status": "pending", "last_finished_at": None, "errors": []}

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def _set(self, **values):
        with self._lock:
            self._state.update(values)

    def collect(self):
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL no configurada")
        with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5) as connection:
            configured = [cloud_connections.get_connection(connection, str(item["id"]), include_credentials=True)
                          for item in cloud_connections.list_connections(connection, enabled_only=True)]
            resources = connection.execute("SELECT * FROM inventory_resources").fetchall()
        now = datetime.now(timezone.utc)
        errors = []
        for item in configured:
            matching = [r for r in resources if r["provider"] == item["provider"]
                        and r["scope_id"] == item["scope_id"] and metrics_for(r)]
            if not matching:
                continue
            try:
                credentials = decrypt_credentials(item["encrypted_credentials"])
                reader = aws_samples if item["provider"] == "aws" else gcp_samples
                samples, failures = reader(matching, credentials, now)
                errors.extend(failures)
                export(samples, os.getenv("VANGUARD_OTLP_METRICS_ENDPOINT", "http://127.0.0.1:4318/v1/metrics"))
            except Exception as exc:
                errors.append(f"{item['name']}: {type(exc).__name__}; verifica permisos, conexión y Collector")
        self._set(status="partial" if errors else "success", errors=errors[:30], last_finished_at=datetime.now(timezone.utc).isoformat())

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
                self._set(status="error", errors=["No fue posible recolectar métricas; revisa la base de datos y la configuración."])
            await asyncio.sleep(interval)


metrics_worker = MetricsWorker()
