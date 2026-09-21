"""Background jobs that keep the persisted cloud inventory current."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from datetime import datetime, timezone
from threading import Lock

import psycopg

from . import cloud_connections
from .cloud_credentials import decrypt_credentials, gcp_credentials
from .collectors.aws import AWSCollector
from .collectors.gcp import GCPCollector
from .database import save_resources, sync_resources

logger = logging.getLogger(__name__)
collection_run_lock = Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _enabled(value: str | None) -> bool:
    return (value or "true").strip().lower() not in {"0", "false", "no", "off"}


class AWSCollectionScheduler:
    """Run one non-overlapping AWS collection at a fixed interval."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._state = {
            "enabled": _enabled(os.getenv("VANGUARD_AUTO_COLLECT_AWS")),
            "running": False,
            "status": "idle",
            "interval_seconds": self._interval_from_environment(),
            "last_started_at": None,
            "last_finished_at": None,
            "last_success_at": None,
            "resources_processed": 0,
            "resources_deleted": 0,
            "error_count": 0,
        }

    @staticmethod
    def _interval_from_environment() -> int:
        raw_value = os.getenv("AWS_COLLECTION_INTERVAL_SECONDS", "60")
        try:
            return max(10, int(raw_value))
        except ValueError:
            logger.warning(
                "AWS_COLLECTION_INTERVAL_SECONDS=%r no es válido; se usarán 60 segundos",
                raw_value,
            )
            return 60

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def configure(self) -> None:
        """Reload settings after the central .env file has been loaded."""
        with self._lock:
            self._state["enabled"] = _enabled(os.getenv("VANGUARD_AUTO_COLLECT_AWS"))
            self._state["interval_seconds"] = self._interval_from_environment()
            if not self._state["enabled"]:
                self._state["status"] = "disabled"
            elif self._state["status"] == "disabled":
                self._state["status"] = "idle"

    def _set_state(self, **values: object) -> None:
        with self._lock:
            self._state.update(values)

    @staticmethod
    def _collect_saved_connection(database_url: str, configured: dict) -> tuple[int, int, list[str]]:
        with collection_run_lock:
            with psycopg.connect(database_url) as connection:
                cloud_connections.set_connection_status(
                    connection, str(configured["id"]), status="syncing"
                )
            credentials = decrypt_credentials(configured["encrypted_credentials"])
            if configured["provider"] == "aws":
                collector = AWSCollector(
                    regions=configured["regions"],
                    credentials=credentials,
                )
                result = collector.collect()
                sync_options = {
                    "provider": "aws",
                    "scope_id": configured["scope_id"],
                    "regions": configured["regions"],
                }
            else:
                collector = GCPCollector(
                    configured["scope_id"],
                    credentials=gcp_credentials(credentials),
                )
                result = collector.collect()
                sync_options = {
                    "provider": "gcp",
                    "scope_id": configured["scope_id"],
                }
            if result.errors:
                saved = save_resources(database_url, result.resources)
                deleted = 0
                connection_status = "partial"
            else:
                saved, deleted = sync_resources(database_url, result.resources, **sync_options)
                connection_status = "success"
            with psycopg.connect(database_url) as connection:
                cloud_connections.set_connection_status(
                    connection,
                    str(configured["id"]),
                    status=connection_status,
                    error="\n".join(result.errors) or None,
                    synced=not result.errors,
                )
            return saved, deleted, result.errors

    @classmethod
    def _collect_configured(cls, database_url: str) -> tuple[int, int, list[str]] | None:
        with psycopg.connect(database_url) as connection:
            cloud_connections.ensure_table(connection)
            configured = cloud_connections.list_connections(connection, enabled_only=True)
            configured = [
                cloud_connections.get_connection(
                    connection, str(item["id"]), include_credentials=True
                )
                for item in configured
            ]
            connection.commit()
        if not configured:
            return None
        saved_total = 0
        deleted_total = 0
        errors: list[str] = []
        for item in configured:
            try:
                saved, deleted, item_errors = cls._collect_saved_connection(database_url, item)
                saved_total += saved
                deleted_total += deleted
                errors.extend(f"{item['name']}: {error}" for error in item_errors)
            except Exception as exc:
                message = f"{item['name']}: {type(exc).__name__}: {exc}"
                errors.append(message)
                with psycopg.connect(database_url) as connection:
                    cloud_connections.set_connection_status(
                        connection,
                        str(item["id"]),
                        status="error",
                        error=f"{type(exc).__name__}: {exc}",
                    )
        return saved_total, deleted_total, errors

    @classmethod
    def _collect_and_save(cls) -> tuple[int, int, list[str]]:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL no está configurada")

        configured_result = cls._collect_configured(database_url)
        if configured_result is not None:
            return configured_result

        collector = AWSCollector(
            profile=os.getenv("AWS_PROFILE") or None,
            regions=_csv(
                os.getenv("AWS_REGIONS")
                or os.getenv("AWS_DEFAULT_REGION")
                or os.getenv("AWS_REGION")
            ),
        )
        result = collector.collect()
        if result.errors:
            saved = save_resources(database_url, result.resources)
            deleted = 0
        else:
            saved, deleted = sync_resources(
                database_url,
                result.resources,
                provider="aws",
                scope_id=collector.account_id,
                regions=collector.regions,
            )
        return saved, deleted, result.errors

    async def collect_once(self) -> None:
        self._set_state(
            running=True,
            status="running",
            last_started_at=_utc_now(),
            resources_deleted=0,
            error_count=0,
        )
        try:
            saved, deleted, errors = await asyncio.to_thread(self._collect_and_save)
            finished_at = _utc_now()
            values = {
                "running": False,
                "status": "partial" if errors else "success",
                "last_finished_at": finished_at,
                "resources_processed": saved,
                "resources_deleted": deleted,
                "error_count": len(errors),
            }
            if not errors:
                values["last_success_at"] = finished_at
            self._set_state(**values)
            if errors:
                logger.warning(
                    "Recolección cloud parcial: %s recursos procesados, %s errores",
                    saved,
                    len(errors),
                )
            else:
                logger.info(
                    "Recolección cloud completa: %s recursos procesados, %s eliminados",
                    saved,
                    deleted,
                )
        except asyncio.CancelledError:
            self._set_state(running=False)
            raise
        except Exception:
            logger.exception("Falló la recolección automática cloud")
            self._set_state(
                running=False,
                status="error",
                last_finished_at=_utc_now(),
                resources_deleted=0,
                error_count=1,
            )

    async def run(self) -> None:
        while True:
            started_at = asyncio.get_running_loop().time()
            await self.collect_once()
            elapsed = asyncio.get_running_loop().time() - started_at
            delay = max(0, self.snapshot()["interval_seconds"] - elapsed)
            await asyncio.sleep(delay)


aws_collection_scheduler = AWSCollectionScheduler()


async def stop_scheduler(task: asyncio.Task | None) -> None:
    if not task:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
