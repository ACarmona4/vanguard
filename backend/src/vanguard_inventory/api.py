"""Local HTTP API backed by the collected PostgreSQL inventory."""

import asyncio
import logging
import os
import secrets
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated

import psycopg
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row

from . import __version__, repository
from .accounts import auth
from .accounts import connections as cloud_connections
from .accounts import deployments as lab_deployments
from .accounts.mailer import send_password_reset
from .accounts.credentials import encrypt_credentials, validate_aws, validate_gcp
from .costs import repository as cost_repository
from .costs.api import create_router as costs_router
from .costs.worker import cost_worker
from .utilization.api import create_router as utilization_router
from .utilization.worker import metrics_worker
from .config import load_environment
from .database import ensure_schema as ensure_inventory_schema
from .scheduler import aws_collection_scheduler, stop_scheduler
from .schemas import (
    AWSCollectionStatus,
    AWSConnectionCreate,
    AuthResponse,
    CloudConnectionCreate,
    CloudConnectionResponse,
    GCPConnectionCreate,
    ForgotPasswordRequest,
    InventorySummary,
    LabDeploymentCreate,
    LabDeploymentResponse,
    LoginRequest,
    PasswordChangeRequest,
    ResetPasswordRequest,
    ResourceFilters,
    ResourcePage,
    ResourceQuery,
    SignUpRequest,
    UserProfileUpdate,
    UserResponse,
)

load_environment()
logger = logging.getLogger(__name__)
SESSION_COOKIE = "vanguard_session"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        with psycopg.connect(database_url) as connection:
            auth.ensure_tables(connection)
            cloud_connections.ensure_table(connection)
            ensure_inventory_schema(connection)
            lab_deployments.ensure_table(connection)
            cost_repository.ensure_schema(connection)
            connection.commit()
    aws_collection_scheduler.configure()
    task = None
    if aws_collection_scheduler.snapshot()["enabled"]:
        task = asyncio.create_task(
            aws_collection_scheduler.run(), name="aws-inventory-collector"
        )
    metrics_task = asyncio.create_task(metrics_worker.run(), name="utilization-collector")
    costs_task = asyncio.create_task(cost_worker.run(), name="cost-collector")
    try:
        yield
    finally:
        await stop_scheduler(metrics_task)
        await stop_scheduler(costs_task)
        await stop_scheduler(task)


app = FastAPI(title="Vanguard Inventory", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Accept", "Content-Type", "X-CSRF-Token"],
)


def get_connection() -> Iterator[psycopg.Connection]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    with psycopg.connect(database_url, connect_timeout=5, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        connection.execute("SET LOCAL statement_timeout = '5s'")
        yield connection


Database = Annotated[psycopg.Connection, Depends(get_connection)]


def get_write_connection() -> Iterator[psycopg.Connection]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    with psycopg.connect(database_url, connect_timeout=5, row_factory=dict_row) as connection:
        yield connection


WriteDatabase = Annotated[psycopg.Connection, Depends(get_write_connection)]


def get_current_user(request: Request) -> dict:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="Account service unavailable")
    with psycopg.connect(database_url, connect_timeout=5, row_factory=dict_row) as connection:
        user = auth.get_session(connection, request.cookies.get(SESSION_COOKIE))
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


CurrentUser = Annotated[dict, Depends(get_current_user)]


def verify_csrf(
    user: CurrentUser,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> dict:
    if not csrf_token or not secrets.compare_digest(csrf_token, user["csrf_token"]):
        raise HTTPException(status_code=403, detail="Invalid security token")
    return user


ProtectedUser = Annotated[dict, Depends(verify_csrf)]
app.include_router(utilization_router(Database, CurrentUser))
app.include_router(costs_router(Database, WriteDatabase, CurrentUser, ProtectedUser))


def _set_session_cookie(response: Response, token: str, expires_at) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=os.getenv("VANGUARD_SECURE_COOKIES", "true").lower() != "false",
        samesite="lax",
        expires=expires_at,
        path="/",
    )


def _auth_response(user: dict, csrf_token: str) -> dict:
    public = {key: user[key] for key in ("id", "email", "full_name", "theme", "created_at", "updated_at")}
    return {"user": public, "csrf_token": csrf_token}


@app.exception_handler(psycopg.Error)
def database_error(_request: Request, _error: psycopg.Error) -> JSONResponse:
    # Connection errors may contain credentials or internal connection details.
    return JSONResponse(status_code=503, content={"detail": "Inventory unavailable"})


@app.post("/api/auth/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignUpRequest, response: Response, connection: WriteDatabase) -> dict:
    try:
        user = auth.create_user(
            connection,
            email=str(payload.email),
            full_name=payload.full_name,
            password=payload.password,
        )
    except psycopg.errors.UniqueViolation as exc:
        connection.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists") from exc
    token, csrf, expires_at = auth.create_session(connection, str(user["id"]))
    _set_session_cookie(response, token, expires_at)
    return _auth_response(user, csrf)


@app.post("/api/auth/login", response_model=AuthResponse)
def login(payload: LoginRequest, request: Request, response: Response, connection: WriteDatabase) -> dict:
    user = auth.authenticate(
        connection,
        str(payload.email),
        payload.password,
        remote_address=request.client.host if request.client else "unknown",
    )
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token, csrf, expires_at = auth.create_session(connection, str(user["id"]))
    _set_session_cookie(response, token, expires_at)
    return _auth_response(user, csrf)


@app.get("/api/auth/me", response_model=AuthResponse)
def me(user: CurrentUser) -> dict:
    return _auth_response(user, user["csrf_token"])


@app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, _user: ProtectedUser, connection: WriteDatabase) -> Response:
    auth.delete_session(connection, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@app.post("/api/auth/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(
    payload: ForgotPasswordRequest,
    connection: WriteDatabase,
    background_tasks: BackgroundTasks,
) -> dict:
    token = auth.create_reset_token(connection, str(payload.email))
    # Delivery is intentionally decoupled from identity. A mail worker can consume
    # this event without granting the API access to an email provider credential.
    if token:
        logger.info("Password reset requested for an existing account")
        background_tasks.add_task(send_password_reset, str(payload.email), token)
    result = {"message": "If the account exists, reset instructions will be sent."}
    if token and os.getenv("VANGUARD_EXPOSE_RESET_TOKEN", "false").lower() == "true":
        result["reset_token"] = token
    return result


@app.post("/api/auth/reset-password")
def password_reset(payload: ResetPasswordRequest, connection: WriteDatabase) -> dict:
    if not auth.reset_password(connection, payload.token, payload.password):
        raise HTTPException(status_code=400, detail="The reset link is invalid or expired")
    return {"message": "Password updated"}


@app.put("/api/account/profile", response_model=UserResponse)
def profile_update(payload: UserProfileUpdate, user: ProtectedUser, connection: WriteDatabase) -> dict:
    return auth.update_profile(
        connection, str(user["id"]), full_name=payload.full_name, theme=payload.theme
    )


@app.put("/api/account/password", status_code=status.HTTP_204_NO_CONTENT)
def password_change(
    payload: PasswordChangeRequest, user: ProtectedUser, connection: WriteDatabase
) -> Response:
    if not auth.change_password(
        connection,
        str(user["id"]),
        current_password=payload.current_password,
        new_password=payload.new_password,
        current_token_hash=user["token_hash"],
    ):
        raise HTTPException(status_code=400, detail="The current password is incorrect")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/health")
def health(connection: Database) -> dict[str, str]:
    connection.execute("SELECT 1 FROM inventory_resources LIMIT 1")
    return {"status": "ok"}


@app.get("/api/resources", response_model=ResourcePage)
def resources(
    query: Annotated[ResourceQuery, Query()], connection: Database, user: CurrentUser
) -> ResourcePage:
    return repository.list_resources(connection, query, str(user["id"]))


@app.get("/api/summary", response_model=InventorySummary)
def summary(
    filters: Annotated[ResourceFilters, Query()], connection: Database, user: CurrentUser
) -> InventorySummary:
    return repository.summarize_resources(connection, filters, str(user["id"]))


@app.get("/api/collection-status", response_model=AWSCollectionStatus)
def collection_status(connection: Database, user: CurrentUser) -> AWSCollectionStatus:
    rows = cloud_connections.list_connections(connection, owner_id=str(user["id"]))
    states = [row["status"] for row in rows]
    last_success = max((row["last_synced_at"] for row in rows if row["last_synced_at"]), default=None)
    status_value = (
        "running" if "syncing" in states else
        "error" if states and all(value == "error" for value in states) else
        "partial" if any(value in {"partial", "error"} for value in states) else
        "success" if rows else "idle"
    )
    total = connection.execute(
        "SELECT count(*) AS total FROM inventory_resources WHERE owner_id = %s", (user["id"],)
    ).fetchone()["total"]
    return AWSCollectionStatus(
        enabled=aws_collection_scheduler.snapshot()["enabled"],
        running="syncing" in states,
        status=status_value,
        interval_seconds=aws_collection_scheduler.snapshot()["interval_seconds"],
        last_started_at=None,
        last_finished_at=last_success,
        last_success_at=last_success,
        resources_processed=total,
        resources_deleted=0,
        error_count=sum(value in {"partial", "error"} for value in states),
    )


@app.get("/api/cloud-connections", response_model=list[CloudConnectionResponse])
def cloud_connection_list(connection: Database, user: CurrentUser) -> list[dict]:
    return cloud_connections.list_connections(connection, owner_id=str(user["id"]))


@app.post(
    "/api/cloud-connections",
    response_model=CloudConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def cloud_connection_create(
    payload: CloudConnectionCreate,
    connection: WriteDatabase,
    background_tasks: BackgroundTasks,
    user: ProtectedUser,
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
            raise ValueError("Unsupported provider")
    except Exception as exc:
        logger.info("Cloud credential validation failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=400,
            detail="Credentials could not be validated. Check permissions, scope, and expiration.",
        ) from exc
    try:
        encrypted_credentials = encrypt_credentials(credentials)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="The credential vault is not configured") from exc
    try:
        created = cloud_connections.create_connection(
            connection,
            owner_id=str(user["id"]),
            provider=payload.provider,
            name=payload.name.strip(),
            scope_id=verified["scope_id"],
            regions=regions,
            credential_hint=verified["credential_hint"],
            identity=verified["identity"],
            encrypted_credentials=encrypted_credentials,
        )
        cost_repository.ensure_source(connection, created)
        connection.commit()
        background_tasks.add_task(_sync_cloud_connection, str(created["id"]), str(user["id"]))
        return created
    except psycopg.errors.UniqueViolation as exc:
        connection.rollback()
        raise HTTPException(
            status_code=409,
            detail="This account or project is already configured",
        ) from exc


def _sync_cloud_connection(connection_id: str, owner_id: str) -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return
    try:
        with psycopg.connect(database_url) as connection:
            configured = cloud_connections.get_connection(
                connection, connection_id, owner_id=owner_id, include_credentials=True
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
                    error=f"{type(exc).__name__}: synchronization failed; check permissions and expiration",
                )
        except Exception:
            pass


@app.put("/api/cloud-connections/{connection_id}", response_model=CloudConnectionResponse)
def cloud_connection_update(
    connection_id: str,
    payload: CloudConnectionCreate,
    connection: WriteDatabase,
    background_tasks: BackgroundTasks,
    user: ProtectedUser,
) -> dict:
    existing = cloud_connections.get_connection(connection, connection_id, owner_id=str(user["id"]))
    if not existing:
        raise HTTPException(status_code=404, detail="Cloud connection not found")
    if payload.provider != existing["provider"]:
        raise HTTPException(status_code=400, detail="The provider cannot be changed")
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
            raise ValueError("The credentials belong to a different account or project")
    except Exception as exc:
        logger.info("Cloud credential validation failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=400,
            detail="Credentials could not be validated. Check permissions, scope, and expiration.",
        ) from exc
    try:
        encrypted_credentials = encrypt_credentials(credentials)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="The credential vault is not configured") from exc
    updated = cloud_connections.update_connection(
        connection,
        connection_id,
        owner_id=str(user["id"]),
        name=payload.name.strip(),
        regions=regions,
        credential_hint=verified["credential_hint"],
        identity=verified["identity"],
        encrypted_credentials=encrypted_credentials,
    )
    background_tasks.add_task(_sync_cloud_connection, connection_id, str(user["id"]))
    return updated


@app.delete("/api/cloud-connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def cloud_connection_delete(
    connection_id: str, connection: WriteDatabase, user: ProtectedUser
) -> Response:
    active_lab = connection.execute(
        "SELECT 1 FROM lab_deployments WHERE connection_id = %s AND owner_id = %s",
        (connection_id, user["id"]),
    ).fetchone()
    if active_lab:
        raise HTTPException(status_code=409, detail="Destroy the dummy lab before disconnecting this account")
    if not cloud_connections.delete_connection(connection, connection_id, owner_id=str(user["id"])):
        raise HTTPException(status_code=404, detail="Cloud connection not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/lab-deployments", response_model=list[LabDeploymentResponse])
def lab_deployment_list(connection: Database, user: CurrentUser) -> list[dict]:
    return lab_deployments.list_deployments(connection, str(user["id"]))


@app.post(
    "/api/lab-deployments",
    response_model=LabDeploymentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def lab_deployment_create(
    payload: LabDeploymentCreate,
    connection: WriteDatabase,
    background_tasks: BackgroundTasks,
    user: ProtectedUser,
) -> dict:
    configured = cloud_connections.get_connection(
        connection, str(payload.connection_id), owner_id=str(user["id"])
    )
    if not configured:
        raise HTTPException(status_code=404, detail="Cloud connection not found")
    region = payload.region or (
        configured["regions"][0] if configured["provider"] == "aws" and configured["regions"] else "us-central1"
    )
    try:
        deployment = lab_deployments.queue_deploy(
            connection,
            owner_id=str(user["id"]),
            connection_id=str(payload.connection_id),
            region=region,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    database_url = os.getenv("DATABASE_URL")
    background_tasks.add_task(lab_deployments.run, database_url, str(deployment["id"]))
    return deployment


@app.delete(
    "/api/lab-deployments/{deployment_id}",
    response_model=LabDeploymentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def lab_deployment_destroy(
    deployment_id: str,
    connection: WriteDatabase,
    background_tasks: BackgroundTasks,
    user: ProtectedUser,
) -> dict:
    deployment = lab_deployments.queue_destroy(
        connection, owner_id=str(user["id"]), deployment_id=deployment_id
    )
    if not deployment:
        raise HTTPException(status_code=404, detail="Active lab deployment not found")
    background_tasks.add_task(
        lab_deployments.run, os.getenv("DATABASE_URL"), deployment_id, destroy=True
    )
    return deployment
