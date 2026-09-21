"""Local HTTP API backed by the collected PostgreSQL inventory."""

import asyncio
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated

import psycopg
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row

from . import __version__, cloud_connections, repository
from .cloud_credentials import encrypt_credentials, validate_aws, validate_gcp
from .utilization.api import create_router as utilization_router
from .utilization.worker import metrics_worker
from .config import load_environment
from .database import ensure_schema as ensure_inventory_schema
from .scheduler import aws_collection_scheduler, stop_scheduler
from .schemas import (
    AWSCollectionStatus,
    AWSConnectionCreate,
    CloudConnectionCreate,
    CloudConnectionResponse,
    GCPConnectionCreate,
    InventorySummary,
    ResourceFilters,
    ResourcePage,
    ResourceQuery,
)

load_environment()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        with psycopg.connect(database_url) as connection:
            ensure_inventory_schema(connection)
            cloud_connections.ensure_table(connection)
            connection.commit()
    aws_collection_scheduler.configure()
    task = None
    if aws_collection_scheduler.snapshot()["enabled"]:
        task = asyncio.create_task(
            aws_collection_scheduler.run(), name="aws-inventory-collector"
        )
    metrics_task = asyncio.create_task(metrics_worker.run(), name="utilization-collector")
    try:
        yield
    finally:
        await stop_scheduler(metrics_task)
        await stop_scheduler(task)


app = FastAPI(title="Vanguard Inventory", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Accept", "Content-Type"],
)


def get_connection() -> Iterator[psycopg.Connection]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL no está configurada")
    with psycopg.connect(database_url, connect_timeout=5, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        connection.execute("SET LOCAL statement_timeout = '5s'")
        yield connection


Database = Annotated[psycopg.Connection, Depends(get_connection)]
app.include_router(utilization_router(Database))


def get_write_connection() -> Iterator[psycopg.Connection]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL no está configurada")
    with psycopg.connect(database_url, connect_timeout=5, row_factory=dict_row) as connection:
        yield connection


WriteDatabase = Annotated[psycopg.Connection, Depends(get_write_connection)]


@app.exception_handler(psycopg.Error)
def database_error(_request: Request, _error: psycopg.Error) -> JSONResponse:
    # Connection errors may contain credentials or internal connection details.
    return JSONResponse(status_code=503, content={"detail": "Inventario no disponible"})


@app.get("/api/health")
def health(connection: Database) -> dict[str, str]:
    connection.execute("SELECT 1 FROM inventory_resources LIMIT 1")
    return {"status": "ok"}


@app.get("/api/resources", response_model=ResourcePage)
def resources(query: Annotated[ResourceQuery, Query()], connection: Database) -> ResourcePage:
    return repository.list_resources(connection, query)


@app.get("/api/summary", response_model=InventorySummary)
def summary(filters: Annotated[ResourceFilters, Query()], connection: Database) -> InventorySummary:
    return repository.summarize_resources(connection, filters)


@app.get("/api/collection-status", response_model=AWSCollectionStatus)
def collection_status() -> AWSCollectionStatus:
    return AWSCollectionStatus(**aws_collection_scheduler.snapshot())


@app.get("/api/cloud-connections", response_model=list[CloudConnectionResponse])
def cloud_connection_list(connection: Database) -> list[dict]:
    return cloud_connections.list_connections(connection)


@app.post(
    "/api/cloud-connections",
    response_model=CloudConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def cloud_connection_create(
    payload: CloudConnectionCreate,
    connection: WriteDatabase,
    background_tasks: BackgroundTasks,
) -> dict:
    try:
        if isinstance(payload, AWSConnectionCreate):
            credentials = {
                "access_key_id": payload.access_key_id,
                "secret_access_key": payload.secret_access_key,
                "session_token": payload.session_token or "",
            }
            verified = validate_aws(credentials, payload.regions)
            regions = payload.regions
        elif isinstance(payload, GCPConnectionCreate):
            credentials = payload.service_account_json
            verified = validate_gcp(credentials, payload.project_id)
            regions = []
        else:  # pragma: no cover - guarded by Pydantic
            raise ValueError("Proveedor no soportado")
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"No fue posible validar las credenciales: {type(exc).__name__}: {exc}",
        ) from exc
    try:
        created = cloud_connections.create_connection(
            connection,
            provider=payload.provider,
            name=payload.name.strip(),
            scope_id=verified["scope_id"],
            regions=regions,
            credential_hint=verified["credential_hint"],
            identity=verified["identity"],
            encrypted_credentials=encrypt_credentials(credentials),
        )
        background_tasks.add_task(_sync_cloud_connection, str(created["id"]))
        return created
    except psycopg.errors.UniqueViolation as exc:
        connection.rollback()
        raise HTTPException(
            status_code=409,
            detail="Esta cuenta o proyecto ya está configurado",
        ) from exc


def _sync_cloud_connection(connection_id: str) -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return
    try:
        with psycopg.connect(database_url) as connection:
            configured = cloud_connections.get_connection(
                connection, connection_id, include_credentials=True
            )
        if configured:
            aws_collection_scheduler._collect_saved_connection(database_url, configured)
    except Exception as exc:
        try:
            with psycopg.connect(database_url) as connection:
                cloud_connections.set_connection_status(
                    connection,
                    connection_id,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                )
        except Exception:
            pass


@app.put("/api/cloud-connections/{connection_id}", response_model=CloudConnectionResponse)
def cloud_connection_update(
    connection_id: str,
    payload: CloudConnectionCreate,
    connection: WriteDatabase,
    background_tasks: BackgroundTasks,
) -> dict:
    existing = cloud_connections.get_connection(connection, connection_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Conexión cloud no encontrada")
    if payload.provider != existing["provider"]:
        raise HTTPException(status_code=400, detail="No se puede cambiar el proveedor")
    try:
        if isinstance(payload, AWSConnectionCreate):
            credentials = {
                "access_key_id": payload.access_key_id,
                "secret_access_key": payload.secret_access_key,
                "session_token": payload.session_token or "",
            }
            verified = validate_aws(credentials, payload.regions)
            regions = payload.regions
        else:
            credentials = payload.service_account_json
            verified = validate_gcp(credentials, payload.project_id)
            regions = []
        if verified["scope_id"] != existing["scope_id"]:
            raise ValueError("Las credenciales pertenecen a otra cuenta o proyecto")
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"No fue posible validar las credenciales: {type(exc).__name__}: {exc}",
        ) from exc
    updated = cloud_connections.update_connection(
        connection,
        connection_id,
        name=payload.name.strip(),
        regions=regions,
        credential_hint=verified["credential_hint"],
        identity=verified["identity"],
        encrypted_credentials=encrypt_credentials(credentials),
    )
    background_tasks.add_task(_sync_cloud_connection, connection_id)
    return updated


@app.delete("/api/cloud-connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def cloud_connection_delete(connection_id: str, connection: WriteDatabase) -> Response:
    if not cloud_connections.delete_connection(connection, connection_id):
        raise HTTPException(status_code=404, detail="Conexión cloud no encontrada")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
