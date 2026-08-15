"""Project membership routes (nested under /api/projects).

The three write routes decide who is a member of the project, and project
membership is what ``authorize_topic_access`` reads to let someone into every
topic of that project. So the acting identity is resolved at the trust boundary
(``ActorResolverDep``) and the service authorizes it — unlike most 2.0 routes
these do NOT honor a handle passed in the body: a claimed handle is exactly the
forgery this surface must not accept. Reading the roster stays open, as it was.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
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
async def add_member(
    project_id: uuid.UUID,
    body: MemberCreate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(fallback_handle=None, project_id=project_id)
    member = await MemberService(db).add(
        project_id=project_id, user_handle=body.user_handle, role=body.role, actor=who
    )
    return ok(MemberOut.model_validate(member).model_dump(mode="json"))


@router.get("/api/projects/{project_id}/members")
async def list_members(project_id: uuid.UUID, db: DbSession) -> dict:
    from app.domain.identity.repositories import AgentBindingRepository
    from app.domain.project.repositories import ProjectRepository
    from app.domain.user.repositories import UserRepository

    members, total = await MemberService(db).list_for_project(project_id)
    # Attach display names (User.name) so the UI can resolve @名字 → handle, and
    # the profile's avatar_id so the chat panel can render the real avatar
    # instead of a colored initial. Both come off the same roster row; a handle
    # with no fusion profile behind it has avatar_id None (→ initial fallback).
    profiles = {
        m["handle"]: m for m in await ProjectRepository(db).list_members(project_id)
    }
    # Same is-agent derivation as the topic roster: a member is an agent iff it
    # carries an AgentBinding — never a handle-string check. The UI badges and
    # filters on this, and every topic's 分身 acts under its own
    # ``cheese-<topic hex>`` handle, so matching the bare string would mis-label
    # any 分身 that ever lands on a project roster.
    users = UserRepository(db)
    rows = {m.user_handle: await users.get_by_handle(m.user_handle) for m in members}
    user_ids = [u.id for u in rows.values() if u is not None]
    agent_ids = await AgentBindingRepository(db).agent_user_ids(user_ids)
    items = []
    for m in members:
        d = MemberOut.model_validate(m).model_dump(mode="json")
        profile = profiles.get(m.user_handle)
        d["name"] = profile["name"] if profile else m.user_handle
        d["avatar_id"] = profile["avatar_id"] if profile else None
        user = rows.get(m.user_handle)
        d["agent"] = user is not None and user.id in agent_ids
        items.append(d)
    return ok(page(items, total))


@router.put("/api/projects/{project_id}/members/{user_handle}")
async def update_member_role(
    project_id: uuid.UUID,
    user_handle: str,
    body: MemberRoleUpdate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(fallback_handle=None, project_id=project_id)
    member = await MemberService(db).update_role(
        project_id=project_id, user_handle=user_handle, role=body.role, actor=who
    )
    return ok(MemberOut.model_validate(member).model_dump(mode="json"))


@router.delete("/api/projects/{project_id}/members/{user_handle}")
async def remove_member(
    project_id: uuid.UUID,
    user_handle: str,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await MemberService(db).remove(
        project_id=project_id, user_handle=user_handle, actor=who
    )
    return ok({"deleted": True})
