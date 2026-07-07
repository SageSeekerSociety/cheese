"""事项 (workitems) — human REST front door. Phase-A contract over the mock store.

Lock model (identical for human and agent owners): claiming an item takes an
exclusive lock — it cannot be preempted and its content freezes. From then on the
only mutation is appending an annotation, which notifies the owner.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.workspace.actor import ok, resolve_actor
from app.domain.workspace.mock_store import STORE
from app.domain.workspace.schemas import (
    AddAnnotationRequest,
    CreateWorkItemRequest,
    UpdateWorkItemStatusRequest,
)

router = APIRouter(tags=["Workspace · 事项"])


@router.get("/projects/{projectId}/workitems", summary="List work items")
async def list_workitems(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    _auth: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    items = STORE.list_workitems(project_id)
    return ok({"workItems": [w.model_dump(by_alias=True) for w in items]})


@router.post(
    "/projects/{projectId}/workitems",
    summary="Create work item",
    status_code=status.HTTP_201_CREATED,
)
async def create_workitem(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    payload: CreateWorkItemRequest,
    auth: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = await resolve_actor(db, auth.user_id)
    item = STORE.create_workitem(
        project_id, actor, payload.title, payload.description, payload.parent_id
    )
    return ok({"workItem": item.model_dump(by_alias=True)}, code=201, message="Created")


@router.get("/workitems/{workItemId}", summary="Get work item")
async def get_workitem(
    work_item_id: Annotated[int, Path(ge=1, alias="workItemId")],
    _auth: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    return ok(STORE.get_workitem(work_item_id).model_dump(by_alias=True))


@router.post("/workitems/{workItemId}/claim", summary="Claim (lock) work item")
async def claim_workitem(
    work_item_id: Annotated[int, Path(ge=1, alias="workItemId")],
    auth: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = await resolve_actor(db, auth.user_id)
    item = STORE.claim_workitem(work_item_id, actor)
    return ok({"workItem": item.model_dump(by_alias=True)})


@router.patch("/workitems/{workItemId}", summary="Update work item status")
async def update_status(
    work_item_id: Annotated[int, Path(ge=1, alias="workItemId")],
    payload: UpdateWorkItemStatusRequest,
    auth: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = await resolve_actor(db, auth.user_id)
    item = STORE.update_status(work_item_id, actor, payload.status)
    return ok({"workItem": item.model_dump(by_alias=True)})


@router.post(
    "/workitems/{workItemId}/annotations",
    summary="Append annotation (notifies owner)",
    status_code=status.HTTP_201_CREATED,
)
async def add_annotation(
    work_item_id: Annotated[int, Path(ge=1, alias="workItemId")],
    payload: AddAnnotationRequest,
    auth: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = await resolve_actor(db, auth.user_id)
    ann = STORE.add_annotation(work_item_id, actor, payload.content)
    return ok({"annotation": ann.model_dump(by_alias=True)}, code=201, message="Created")
