"""Periodic cost ingestion isolated by saved cloud connection."""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import psycopg
from psycopg.rows import dict_row

from ..accounts.credentials import decrypt_credentials
from .collectors import collect_aws, collect_gcp
from . import repository

logger = logging.getLogger(__name__)


class CostWorker:
    @staticmethod
    def _source_rows(
        database_url: str,
        owner_id: str | None = None,
        connection_id: str | None = None,
    ):
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            repository.ensure_schema(connection)
            clauses = ["s.enabled", "s.status <> 'unconfigured'", "c.enabled"]
            values = []
            if owner_id:
                clauses.append("s.owner_id = %s")
                values.append(owner_id)
            if connection_id:
                clauses.append("s.connection_id = %s")
                values.append(connection_id)
            rows = connection.execute(f"""
                SELECT s.*, c.scope_id, c.encrypted_credentials
                FROM cost_sources s JOIN cloud_connections c ON c.id = s.connection_id
                WHERE {' AND '.join(clauses)}
                ORDER BY s.updated_at
            """, values).fetchall()
            connection.commit()
            return rows

    @staticmethod
    def _collect_source(database_url: str, source: dict, force: bool = False) -> None:
        with psycopg.connect(database_url) as connection:
            if not repository.claim_source(
                connection, str(source["connection_id"]), force=force
            ):
                return
        try:
            credentials = decrypt_credentials(source["encrypted_credentials"])
            end = date.today() + timedelta(days=1)
            with psycopg.connect(database_url) as connection:
                start = repository.collection_start(
                    connection, str(source["connection_id"]), end
                )
            collector = collect_aws if source["provider"] == "aws" else collect_gcp
            records, detail_available = collector(source, credentials, start, end)
            with psycopg.connect(database_url) as connection:
                repository.save_records(
                    connection,
                    source=source,
                    records=records,
                    start=start,
                    end=end,
                    detail_available=detail_available,
                )
        except Exception as exc:
            logger.warning("Cost collection failed for %s: %s", source["connection_id"], type(exc).__name__)
            with psycopg.connect(database_url) as connection:
                repository.set_source_error(
                    connection,
                    str(source["connection_id"]),
                    f"{type(exc).__name__}: cost data could not be read; check billing permissions and configuration",
                )

    def collect(
        self,
        owner_id: str | None = None,
        force: bool = False,
        connection_id: str | None = None,
    ) -> None:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        sources = self._source_rows(database_url, owner_id, connection_id)
        if not sources:
            return
        with ThreadPoolExecutor(max_workers=min(4, len(sources)), thread_name_prefix="cost-collector") as executor:
            futures = [
                executor.submit(self._collect_source, database_url, source, force)
                for source in sources
            ]
            for future in as_completed(futures):
                future.result()

    async def run(self) -> None:
        try:
            interval = max(3600, int(os.getenv("VANGUARD_COST_INTERVAL_SECONDS", "21600")))
        except ValueError:
            interval = 21600
        while True:
            try:
                await asyncio.to_thread(self.collect)
            except Exception:
                logger.exception("Periodic cost collection failed")
            await asyncio.sleep(interval)


cost_worker = CostWorker()
