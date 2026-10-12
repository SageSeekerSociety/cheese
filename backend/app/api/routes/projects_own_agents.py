"""Members' own Claude Code in a project (#2991): whether the project lets
members bring theirs, and whose are here.

A member's own Claude Code sends the project's content to the model through
its owner's own account, under that account's terms and settings. A project
whose content must not leave that way is closed to them here, by the people who
manage it.
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.agent.device_hub import device_hub
from app.domain.agent_instance.own import (
    SETTING,
    OwnCallers,
    allows_own_agents,
    listing,
    owned_by_seat,
    set_callers,
)
from app.domain.membership.services import MemberService
from app.domain.project.services import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


class OwnAgentsUpdate(BaseModel):
    allowed: bool


class OwnAgentCallersUpdate(BaseModel):
    callers: OwnCallers


class OwnAgentMachineOut(BaseModel):
    name: str
    online: bool


class OwnAgentOut(BaseModel):
    handle: str
    name: str | None
    callers: OwnCallers
    owner_handle: str | None
    owner_name: str | None
    machines: list[OwnAgentMachineOut]


class OwnAgentsOut(BaseModel):
    allowed: bool
    can_manage: bool
    agents: list[OwnAgentOut]


@router.get("/{project_id}/own-agents")
async def get_own_agents(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    can_manage = True
    try:
        await MemberService(db).require_manager(project_id, actor)
    except ForbiddenError:
        can_manage = False
    return ok(
        {
            "allowed": allows_own_agents(project.settings),
            "can_manage": can_manage,
            "agents": await listing(db, project_id, device_hub.is_online),
        }
    )


@router.put("/{project_id}/own-agents")
async def set_own_agents(
    project_id: uuid.UUID,
    body: OwnAgentsUpdate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Let members bring their own Claude Code into the project, or not. Turned
    off, no member's own agent is called in it; turned on again, they are."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require_manager(project_id, actor)
    service = ProjectService(db)
    project = await service.get_or_404(project_id)
    await service.merge_settings(project, {SETTING: body.allowed})
    return await get_own_agents(project_id, db, resolver)


@router.put(
    "/{project_id}/own-agents/{handle}/callers",
    dependencies=[ROUTE_DECIDES],
    **typed_response(OwnAgentsOut),
)
async def set_own_agent_callers(
    project_id: uuid.UUID,
    handle: str,
    body: OwnAgentCallersUpdate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Who may call a member's own agent: its owner alone, or any person in the
    project. Only its owner decides, since it is their account that pays."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    owned = await owned_by_seat(db, project_id, handle)
    if owned is None:
        raise NotFoundError(say("ownAgentNotFound"))
    if actor.user_id is None or actor.user_id != owned.owner_user_id:
        raise ForbiddenError(say("ownAgentCallersOwnerOnly"))
    await set_callers(db, owned.instance_id, body.callers)
    await db.commit()
    return await get_own_agents(project_id, db, resolver)
