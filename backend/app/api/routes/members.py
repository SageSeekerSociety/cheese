"""Project membership routes (nested under /api/projects)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.db import get_db
from app.domain.membership.schemas import (
    MemberCreate,
    MemberOut,
    MemberRoleUpdate,
)
from app.domain.membership.services import MemberService

router = APIRouter(prefix="", tags=["members"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("/api/projects/{project_id}/members")
async def add_member(project_id: uuid.UUID, body: MemberCreate, db: DbSession) -> dict:
    member = await MemberService(db).add(
        project_id=project_id, user_handle=body.user_handle, role=body.role
    )
    return ok(MemberOut.model_validate(member).model_dump(mode="json"))


@router.get("/api/projects/{project_id}/members")
async def list_members(project_id: uuid.UUID, db: DbSession) -> dict:
    from app.domain.project.repositories import ProjectRepository

    members, total = await MemberService(db).list_for_project(project_id)
    # Attach display names (User.name) so the UI can resolve @名字 → handle.
    names = {
        m["handle"]: m["name"]
        for m in await ProjectRepository(db).list_members(project_id)
    }
    items = []
    for m in members:
        d = MemberOut.model_validate(m).model_dump(mode="json")
        d["name"] = names.get(m.user_handle, m.user_handle)
        items.append(d)
    return ok(page(items, total))


@router.put("/api/projects/{project_id}/members/{user_handle}")
async def update_member_role(
    project_id: uuid.UUID,
    user_handle: str,
    body: MemberRoleUpdate,
    db: DbSession,
) -> dict:
    member = await MemberService(db).update_role(
        project_id=project_id, user_handle=user_handle, role=body.role
    )
    return ok(MemberOut.model_validate(member).model_dump(mode="json"))


@router.delete("/api/projects/{project_id}/members/{user_handle}")
async def remove_member(project_id: uuid.UUID, user_handle: str, db: DbSession) -> dict:
    await MemberService(db).remove(project_id=project_id, user_handle=user_handle)
    return ok({"deleted": True})
