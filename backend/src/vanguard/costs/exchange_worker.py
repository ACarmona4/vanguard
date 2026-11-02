"""Daily persisted TRM refresh; hourly retries retain the last successful cache."""

import asyncio
import logging
import os

import psycopg

from .exchange import claim_refresh, fetch_rates, save_rates

logger = logging.getLogger(__name__)


class ExchangeRateWorker:
    def collect(self, *, force=False):
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            return
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            if not claim_refresh(connection, force=force):
                return
        try:
            rates = fetch_rates()
            with psycopg.connect(database_url, connect_timeout=5) as connection:
                save_rates(connection, rates)
        except Exception as exc:
            logger.warning("Official TRM refresh failed: %s", type(exc).__name__)
            with psycopg.connect(database_url, connect_timeout=5) as connection:
                connection.execute("UPDATE cost_exchange_sync SET last_error = %s WHERE singleton", ("The official TRM could not be refreshed; the last successful cache is retained.",))
                connection.commit()

    async def run(self):
        while True:
            try:
                await asyncio.to_thread(self.collect)
            except Exception:
                logger.exception("TRM scheduler failed")
            await asyncio.sleep(3600)


exchange_rate_worker = ExchangeRateWorker()
