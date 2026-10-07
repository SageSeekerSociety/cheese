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
from app.api.response import ok
from app.api.routes.projects import DbSession
from app.core.errors import ForbiddenError
from app.domain.agent.device_hub import device_hub
from app.domain.agent_instance.own import SETTING, allows_own_agents, listing
from app.domain.membership.services import MemberService
from app.domain.project.services import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


class OwnAgentsUpdate(BaseModel):
    allowed: bool


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
    project = await ProjectService(db).get_or_404(project_id)
    project.settings = {**(project.settings or {}), SETTING: body.allowed}
    await db.flush()
    return await get_own_agents(project_id, db, resolver)
