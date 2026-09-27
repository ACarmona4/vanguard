from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from . import repository
from .schemas import (
    CostOverview,
    CostQuery,
    CostSourceResponse,
    CostSourceUpdate,
)
from .worker import cost_worker


def create_router(database_dependency, write_database_dependency, user_dependency, protected_user_dependency):
    router = APIRouter(prefix="/api/costs", tags=["costs"])

    @router.get("", response_model=CostOverview)
    def costs(
        connection: database_dependency,
        user: user_dependency,
        query: Annotated[CostQuery, Query()],
    ):
        return repository.overview(
            connection,
            owner_id=str(user["id"]),
            days=query.days,
            provider=query.provider,
        )

    @router.get("/sources", response_model=list[CostSourceResponse])
    def sources(connection: database_dependency, user: user_dependency):
        return repository.list_sources(connection, str(user["id"]))

    @router.put("/sources/{connection_id}", response_model=CostSourceResponse)
    def source_update(
        connection_id: str,
        payload: CostSourceUpdate,
        connection: write_database_dependency,
        background_tasks: BackgroundTasks,
        user: protected_user_dependency,
    ):
        updated = repository.configure_gcp_source(
            connection,
            owner_id=str(user["id"]),
            connection_id=connection_id,
            billing_export_table=payload.billing_export_table,
            billing_location=payload.billing_location,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="GCP cost source not found")
        background_tasks.add_task(
            cost_worker.collect, str(user["id"]), True, connection_id
        )
        return updated

    @router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
    def sync(
        background_tasks: BackgroundTasks,
        user: protected_user_dependency,
    ):
        background_tasks.add_task(cost_worker.collect, str(user["id"]), True)
        return {"message": "Cost synchronization started"}

    return router
