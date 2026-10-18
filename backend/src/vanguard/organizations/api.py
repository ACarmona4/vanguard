"""Organization membership routes for the active workspace."""

import secrets
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response

from ..accounts import auth
from ..accounts.schemas import AuthResponse
from . import repository as organizations
from .schemas import (
    MemberCreate,
    MemberResponse,
    MemberUpdate,
    OrganizationCreate,
    OrganizationResponse,
)


def create_router(
    database_dependency,
    write_dependency,
    user_dependency,
    protected_dependency,
    admin_dependency,
    read_admin_dependency,
):
    router = APIRouter(prefix="/api/organizations", tags=["organizations"])

    @router.get("", response_model=list[OrganizationResponse])
    def list_workspaces(connection: database_dependency, user: user_dependency):
        return organizations.list_organizations(connection, user["id"])

    @router.post("", response_model=OrganizationResponse, status_code=201)
    def create_workspace(
        payload: OrganizationCreate,
        connection: write_dependency,
        user: protected_dependency,
    ):
        return organizations.create_organization(connection, user["id"], payload.name)

    @router.post("/{organization_id}/switch", response_model=AuthResponse)
    def switch(
        organization_id: UUID,
        request: Request,
        connection: write_dependency,
        user: protected_dependency,
    ):
        csrf = secrets.token_urlsafe(24)
        if not organizations.switch_organization(
            connection, user, organization_id, csrf
        ):
            raise HTTPException(404, "Organization not found")
        current = auth.get_session(connection, request.cookies.get("vanguard_session"))
        if not current:
            raise HTTPException(401, "Organization membership is no longer available")
        public = {
            key: current[key]
            for key in (
                "id",
                "email",
                "full_name",
                "theme",
                "created_at",
                "updated_at",
                "organization_id",
                "organization_name",
                "role",
            )
        }
        return {"user": public, "csrf_token": csrf}

    @router.get("/members", response_model=list[MemberResponse])
    def members(connection: database_dependency, user: read_admin_dependency):
        return organizations.list_members(connection, user["organization_id"])

    @router.post("/members", status_code=201)
    def add(
        payload: MemberCreate, connection: write_dependency, user: admin_dependency
    ):
        result = organizations.add_member(
            connection,
            user["organization_id"],
            auth.normalize_email(payload.email),
            payload.role,
        )
        if result is None:
            raise HTTPException(
                404, "Create a Vanguard account for this email before adding it"
            )
        if not result:
            raise HTTPException(409, "This user is already a member")
        return {"message": "Member added"}

    @router.put("/members/{user_id}", status_code=204)
    def update(
        user_id: UUID,
        payload: MemberUpdate,
        connection: write_dependency,
        user: admin_dependency,
    ):
        if str(user_id) == str(user["id"]):
            raise HTTPException(409, "Ask another administrator to change your role")
        if not organizations.change_member(
            connection, user["organization_id"], user_id, payload.role
        ):
            raise HTTPException(
                404, "Editable member not found; ownership is protected"
            )
        return Response(status_code=204)

    @router.delete("/members/{user_id}", status_code=204)
    def remove(user_id: UUID, connection: write_dependency, user: admin_dependency):
        if str(user_id) == str(user["id"]):
            raise HTTPException(
                409, "Ask another administrator to remove your membership"
            )
        if not organizations.change_member(
            connection, user["organization_id"], user_id
        ):
            raise HTTPException(
                404, "Removable member not found; ownership is protected"
            )
        return Response(status_code=204)

    return router
