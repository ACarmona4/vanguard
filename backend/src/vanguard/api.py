"""Application entry point, shared dependencies, and HTTP route composition."""

import asyncio
import logging
import os
import secrets
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated

import psycopg
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from . import __version__
from .accounts import auth
from .accounts.mailer import send_password_reset
from .accounts.schemas import (
    AuthResponse,
    ForgotPasswordRequest,
    LoginRequest,
    PasswordChangeRequest,
    ResetPasswordRequest,
    SignUpRequest,
    UserProfileUpdate,
    UserResponse,
)
from .connections import repository as cloud_connections
from .connections.credentials import (
    CredentialValidationError,
    encrypt_credentials,
    validate_aws,
    validate_gcp,
)
from .connections.schemas import (
    AWSConnectionCreate,
    CloudConnectionCreate,
    CloudConnectionResponse,
    GCPConnectionCreate,
)
from .core.config import load_environment
from .core.security import (
    APISecurityMiddleware,
    allowed_origins,
    validate_production_settings,
)
from .costs import repository as cost_repository
from .costs.api import create_router as costs_router
from .costs.worker import cost_worker
from .inventory import repository
from .inventory.database import ensure_schema as ensure_inventory_schema
from .inventory.scheduler import cloud_collection_scheduler, stop_scheduler
from .inventory.schemas import (
    CloudCollectionStatus,
    InventorySummary,
    ResourceFilters,
    ResourcePage,
    ResourceQuery,
)
from .labs import service as lab_deployments
from .labs.schemas import (
    LabDeploymentCreate,
    LabDeploymentResponse,
)
from .organizations import repository as organizations
from .organizations.api import create_router as organizations_router
from .recommendations.api import create_router as recommendations_router
from .utilization.api import create_router as utilization_router
from .utilization.worker import metrics_worker

load_environment()
logger = logging.getLogger(__name__)
SESSION_COOKIE = "vanguard_session"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_production_settings()
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            auth.ensure_tables(connection)
            organizations.ensure_tables(connection)
            cloud_connections.ensure_table(connection)
            ensure_inventory_schema(connection)
            lab_deployments.ensure_table(connection)
            cost_repository.ensure_schema(connection)
            organizations.ensure_schema(connection)
            connection.commit()
    cloud_collection_scheduler.configure()
    task = None
    if cloud_collection_scheduler.snapshot()["enabled"]:
        task = asyncio.create_task(
            cloud_collection_scheduler.run(), name="cloud-inventory-collector"
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
    allow_origins=sorted(allowed_origins()),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Accept", "Content-Type", "X-CSRF-Token", "X-Organization-ID"],
)
app.add_middleware(APISecurityMiddleware)


def _request_connection(database_url):
    options = conninfo_to_dict(database_url).get("options", "")
    return psycopg.connect(database_url, connect_timeout=5, row_factory=dict_row,
                           options=options + " -c statement_timeout=5000 -c lock_timeout=3000")


def get_connection() -> Iterator[psycopg.Connection]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    with _request_connection(database_url) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        yield connection


Database = Annotated[psycopg.Connection, Depends(get_connection)]


def get_write_connection() -> Iterator[psycopg.Connection]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    with _request_connection(database_url) as connection:
        yield connection


WriteDatabase = Annotated[psycopg.Connection, Depends(get_write_connection)]


def get_current_user(request: Request) -> dict:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="Account service unavailable")
    with _request_connection(database_url) as connection:
        user = auth.get_session(connection, request.cookies.get(SESSION_COOKIE))
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if request.url.path != "/api/auth/me":
        requested_organization = request.headers.get("X-Organization-ID")
        if not requested_organization:
            raise HTTPException(400, "Select an organization before accessing workspace data")
        if requested_organization != str(user["organization_id"]):
            raise HTTPException(409, "The organization changed in another tab. Reload the page to continue.")
    return user


CurrentUser = Annotated[dict, Depends(get_current_user)]


def verify_csrf(
    user: CurrentUser,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> dict:
    if not csrf_token or not secrets.compare_digest(csrf_token.encode(), user["csrf_token"].encode()):
        raise HTTPException(status_code=403, detail="Invalid security token")
    return user


ProtectedUser = Annotated[dict, Depends(verify_csrf)]


def require_admin(user: ProtectedUser) -> dict:
    if user["role"] not in organizations.ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="An organization administrator is required")
    return user


def require_operator(user: ProtectedUser) -> dict:
    if user["role"] not in organizations.OPERATOR_ROLES:
        raise HTTPException(status_code=403, detail="This role has read-only access")
    return user


def require_read_admin(user: CurrentUser) -> dict:
    return require_admin(user)


AdminUser = Annotated[dict, Depends(require_admin)]
ReadAdminUser = Annotated[dict, Depends(require_read_admin)]
OperatorUser = Annotated[dict, Depends(require_operator)]
app.include_router(utilization_router(Database, CurrentUser))
app.include_router(recommendations_router(Database, CurrentUser))
app.include_router(costs_router(Database, WriteDatabase, CurrentUser, OperatorUser, AdminUser))
app.include_router(organizations_router(Database, WriteDatabase, CurrentUser, ProtectedUser, AdminUser, ReadAdminUser))


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
    public = {key: user[key] for key in ("id", "email", "full_name", "theme", "created_at", "updated_at", "organization_id", "organization_name", "role")}
    return {"user": public, "csrf_token": csrf_token}


@app.exception_handler(psycopg.Error)
def database_error(_request: Request, _error: psycopg.Error) -> JSONResponse:
    # Connection errors may contain credentials or internal connection details.
    return JSONResponse(status_code=503, content={"detail": "Inventory unavailable"})


@app.exception_handler(RequestValidationError)
def validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
    # Pydantic's default includes submitted input, including cloud credentials.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]}
        for item in error.errors()
    ]})


@app.post("/api/auth/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignUpRequest, request: Request, response: Response, connection: WriteDatabase) -> dict:
    if not auth.reserve_attempt(connection, "signup", request.client.host if request.client else "unknown", limit=10):
        raise HTTPException(429, "Too many attempts. Try again later.")
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
    return _auth_response(auth.get_session(connection, token), csrf)


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
    return _auth_response(auth.get_session(connection, token), csrf)


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
    request: Request,
    connection: WriteDatabase,
    background_tasks: BackgroundTasks,
) -> dict:
    if not auth.reserve_attempt(connection, "forgot", request.client.host if request.client else "unknown", limit=5):
        return {"message": "If the account exists, reset instructions will be sent."}
    token = auth.create_reset_token(connection, str(payload.email))
    # Delivery is intentionally decoupled from identity. A mail worker can consume
    # this event without granting the API access to an email provider credential.
    if token:
        logger.info("Password reset requested for an existing account")
        background_tasks.add_task(send_password_reset, str(payload.email), token)
    result = {"message": "If the account exists, reset instructions will be sent."}
    if token and os.getenv("VANGUARD_ENVIRONMENT", "development").lower() != "production" and os.getenv("VANGUARD_EXPOSE_RESET_TOKEN", "false").lower() == "true":
        result["reset_token"] = token
    return result


@app.post("/api/auth/reset-password")
def password_reset(payload: ResetPasswordRequest, request: Request, connection: WriteDatabase) -> dict:
    if not auth.reserve_attempt(connection, "reset", request.client.host if request.client else "unknown", limit=10):
        raise HTTPException(429, "Too many attempts. Try again later.")
    if not auth.reset_password(connection, payload.token, payload.password):
        raise HTTPException(status_code=400, detail="The reset link is invalid or expired")
    return {"message": "Password updated"}


@app.put("/api/account/profile", response_model=UserResponse)
def profile_update(payload: UserProfileUpdate, user: ProtectedUser, connection: WriteDatabase) -> dict:
    updated = auth.update_profile(
        connection, str(user["id"]), full_name=payload.full_name, theme=payload.theme
    )
    return {**updated, **{key: user[key] for key in ("organization_id", "organization_name", "role")}}


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
    return repository.list_resources(connection, query, str(user["organization_id"]))


@app.get("/api/summary", response_model=InventorySummary)
def summary(
    filters: Annotated[ResourceFilters, Query()], connection: Database, user: CurrentUser
) -> InventorySummary:
    return repository.summarize_resources(connection, filters, str(user["organization_id"]))


@app.get("/api/collection-status", response_model=CloudCollectionStatus)
def collection_status(connection: Database, user: CurrentUser) -> CloudCollectionStatus:
    rows = cloud_connections.list_connections(connection, owner_id=str(user["organization_id"]))
    states = [row["status"] for row in rows]
    last_success = max((row["last_synced_at"] for row in rows if row["last_synced_at"]), default=None)
    status_value = (
        "running" if "syncing" in states else
        "error" if states and all(value == "error" for value in states) else
        "partial" if any(value in {"partial", "error"} for value in states) else
        "success" if rows else "idle"
    )
    total = connection.execute(
        "SELECT count(*) AS total FROM inventory_resources WHERE owner_id = %s", (user["organization_id"],)
    ).fetchone()["total"]
    return CloudCollectionStatus(
        enabled=cloud_collection_scheduler.snapshot()["enabled"],
        running="syncing" in states,
        status=status_value,
        interval_seconds=cloud_collection_scheduler.snapshot()["interval_seconds"],
        last_started_at=None,
        last_finished_at=last_success,
        last_success_at=last_success,
        resources_processed=total,
        resources_deleted=0,
        error_count=sum(value in {"partial", "error"} for value in states),
    )


@app.get("/api/cloud-connections", response_model=list[CloudConnectionResponse])
def cloud_connection_list(connection: Database, user: CurrentUser) -> list[dict]:
    return cloud_connections.list_connections(connection, owner_id=str(user["organization_id"]))


@app.post(
    "/api/cloud-connections",
    response_model=CloudConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def cloud_connection_create(
    payload: CloudConnectionCreate,
    connection: WriteDatabase,
    background_tasks: BackgroundTasks,
    user: AdminUser,
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
    except CredentialValidationError as exc:
        logger.info("Cloud credential format validation failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.info("Cloud credential validation failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=400,
            detail="Credentials could not be validated. Check permissions, scope, and expiration.",
        ) from exc
    try:
        encrypted_credentials = encrypt_credentials(credentials, owner_id=str(user["organization_id"]))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="The credential vault is not configured") from exc
    try:
        created = cloud_connections.create_connection(
            connection,
            owner_id=str(user["organization_id"]),
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
        background_tasks.add_task(_sync_cloud_connection, str(created["id"]), str(user["organization_id"]))
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
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            configured = cloud_connections.get_connection(
                connection, connection_id, owner_id=owner_id, include_credentials=True
            )
        if configured:
            cloud_collection_scheduler.collect_saved_connection(database_url, configured)
    except Exception as exc:
        try:
            with psycopg.connect(database_url, connect_timeout=5) as connection:
                cloud_connections.set_connection_status(
                    connection,
                    connection_id,
                    owner_id=owner_id,
                    status="error",
                    error=f"{type(exc).__name__}: synchronization failed; check permissions and expiration",
                )
        except Exception as status_error:
            logger.warning("Could not record connection sync failure: %s", type(status_error).__name__)


@app.put("/api/cloud-connections/{connection_id}", response_model=CloudConnectionResponse)
def cloud_connection_update(
    connection_id: str,
    payload: CloudConnectionCreate,
    connection: WriteDatabase,
    background_tasks: BackgroundTasks,
    user: AdminUser,
) -> dict:
    existing = cloud_connections.get_connection(connection, connection_id, owner_id=str(user["organization_id"]))
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
    except CredentialValidationError as exc:
        logger.info("Cloud credential format validation failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.info("Cloud credential validation failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=400,
            detail="Credentials could not be validated. Check permissions, scope, and expiration.",
        ) from exc
    try:
        encrypted_credentials = encrypt_credentials(credentials, owner_id=str(user["organization_id"]))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="The credential vault is not configured") from exc
    updated = cloud_connections.update_connection(
        connection,
        connection_id,
        owner_id=str(user["organization_id"]),
        name=payload.name.strip(),
        regions=regions,
        credential_hint=verified["credential_hint"],
        identity=verified["identity"],
        encrypted_credentials=encrypted_credentials,
    )
    background_tasks.add_task(_sync_cloud_connection, connection_id, str(user["organization_id"]))
    return updated


@app.delete("/api/cloud-connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def cloud_connection_delete(
    connection_id: str, connection: WriteDatabase, user: AdminUser
) -> Response:
    active_lab = connection.execute(
        "SELECT 1 FROM lab_deployments WHERE connection_id = %s AND owner_id = %s",
        (connection_id, user["organization_id"]),
    ).fetchone()
    if active_lab:
        raise HTTPException(status_code=409, detail="Destroy the dummy lab before disconnecting this account")
    if not cloud_connections.delete_connection(connection, connection_id, owner_id=str(user["organization_id"])):
        raise HTTPException(status_code=404, detail="Cloud connection not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/lab-deployments", response_model=list[LabDeploymentResponse])
def lab_deployment_list(connection: Database, user: CurrentUser) -> list[dict]:
    return lab_deployments.list_deployments(connection, str(user["organization_id"]))


@app.post(
    "/api/lab-deployments",
    response_model=LabDeploymentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def lab_deployment_create(
    payload: LabDeploymentCreate,
    connection: WriteDatabase,
    background_tasks: BackgroundTasks,
    user: AdminUser,
) -> dict:
    configured = cloud_connections.get_connection(
        connection, str(payload.connection_id), owner_id=str(user["organization_id"])
    )
    if not configured:
        raise HTTPException(status_code=404, detail="Cloud connection not found")
    region = payload.region or (
        configured["regions"][0] if configured["provider"] == "aws" and configured["regions"] else "us-central1"
    )
    try:
        deployment = lab_deployments.queue_deploy(
            connection,
            owner_id=str(user["organization_id"]),
            connection_id=str(payload.connection_id),
            region=region,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    database_url = os.getenv("DATABASE_URL")
    background_tasks.add_task(lab_deployments.run, database_url, str(deployment["id"]), owner_id=str(user["organization_id"]))
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
    user: AdminUser,
) -> dict:
    deployment = lab_deployments.queue_destroy(
        connection, owner_id=str(user["organization_id"]), deployment_id=deployment_id
    )
    if not deployment:
        raise HTTPException(status_code=404, detail="Active lab deployment not found")
    background_tasks.add_task(
        lab_deployments.run, os.getenv("DATABASE_URL"), deployment_id, owner_id=str(user["organization_id"]), destroy=True
    )
    return deployment
