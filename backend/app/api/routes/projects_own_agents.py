"""Members' own Claude Code in a project (#2991): whether the project lets
members bring theirs, which kinds of model connection they may use, and whose
are here.

A member's own Claude Code sends the project's content to the model through
its owner's own account, under that account's terms and settings. So a project
lets them in only when someone who manages it turns that on, and may allow only
some kinds of connection — a Claude subscription, an Anthropic API key, another
model service.

Each one is Claude Code with one of its owner's model connections, and a person
may bring several. Its owner adds it to the project and removes it; nothing
else brings it in.
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.agent.device_hub import device_hub
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent_instance.own import (
    KINDS_SETTING,
    SETTING,
    OwnCallers,
    add,
    allowed_kinds,
    allows_own_agents,
    listing,
    my_connections,
    owned_by_seat,
    owned_in_project,
    remove,
    set_callers,
)
from app.domain.device.login_kind import ConnectionKind
from app.domain.identity.handles import agent_instance_handle
from app.domain.membership.services import MemberService
from app.domain.project.services import ProjectService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/projects", tags=["projects"])


class OwnAgentsUpdate(BaseModel):
    """What a manager changes; a field left out stays as it is."""

    allowed: bool | None = None
    # The kinds of connection members' own agents may use here. At least one:
    # allowing none is turning them off, which ``allowed`` says.
    kinds: list[ConnectionKind] | None = Field(default=None, min_length=1)


class OwnAgentCallersUpdate(BaseModel):
    callers: OwnCallers


class OwnAgentAdd(BaseModel):
    # One of the caller's model connections (`my_connections[].id`): named by
    # the machine holding it, or by the connection their account holds.
    connection_id: str = Field(min_length=1, max_length=64)


class OwnAgentMachineOut(BaseModel):
    name: str
    online: bool


class OwnAgentConnectionOut(BaseModel):
    kind: ConnectionKind
    # A subscription's plan (`max`, `pro`, …).
    plan: str | None
    # The model a model service is called with.
    model: str | None
    # Held by its owner's account, so it runs on the platform rather than on
    # one of its owner's machines.
    on_account: bool


class OwnAgentOut(BaseModel):
    handle: str
    name: str | None
    callers: OwnCallers
    owner_handle: str | None
    owner_name: str | None
    # The connection it runs on; None once its owner removed or logged it out.
    connection: OwnAgentConnectionOut | None
    # The machine holding it: only for the reader's own agent, None otherwise.
    machine: OwnAgentMachineOut | None


class MyConnectionOut(BaseModel):
    """One of the reader's own connections, to add to this project."""

    id: str
    kind: ConnectionKind
    plan: str | None
    model: str | None
    # What the reader named a connection their account holds.
    name: str | None
    # A model service's host.
    service_host: str | None
    # Held by the reader's account rather than one of their machines.
    on_account: bool
    # The machine holding it; None when the account does.
    machine_name: str | None
    # Whether that machine is online; one the account holds always is.
    online: bool
    # Already here as one of the reader's own agents.
    added: bool
    # Of a kind the project allows.
    allowed: bool


class OwnAgentsOut(BaseModel):
    allowed: bool
    kinds: list[ConnectionKind]
    can_manage: bool
    agents: list[OwnAgentOut]
    # Empty while the project is closed to members' own agents.
    my_connections: list[MyConnectionOut]


@router.get(
    "/{project_id}/own-agents",
    **typed_response(OwnAgentsOut),
)
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
    allowed = allows_own_agents(project.settings)
    kinds = allowed_kinds(project.settings)
    mine = (
        await my_connections(
            db, project_id, actor.user_id, project.settings, device_hub.is_online
        )
        if allowed and actor.user_id is not None
        else []
    )
    return ok(
        {
            "allowed": allowed,
            "kinds": [kind.value for kind in ConnectionKind if kind in kinds],
            "can_manage": can_manage,
            # Withdrawn from a project closed to them: none is here.
            "agents": await listing(db, project_id, actor.user_id, device_hub.is_online)
            if allowed
            else [],
            "my_connections": mine,
        }
    )


@router.put(
    "/{project_id}/own-agents",
    **typed_response(OwnAgentsOut),
)
async def set_own_agents(
    project_id: uuid.UUID,
    body: OwnAgentsUpdate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Let members bring their own Claude Code into the project, or not, and
    which kinds of model connection it may use. Turned off, members' own
    agents leave the project: off its roster and out of its channels. Turned on
    again, the ones their owners had added are back on the roster, and sit in a
    channel again once someone they answer to names them there."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require_manager(project_id, actor)
    service = ProjectService(db)
    project = await service.get_or_404(project_id)
    patch: dict[str, object] = {}
    if body.allowed is not None:
        patch[SETTING] = body.allowed
    if body.kinds is not None:
        patch[KINDS_SETTING] = [k.value for k in ConnectionKind if k in body.kinds]
    await service.merge_settings(project, patch)
    if body.allowed is False:
        for instance, _owner, _callers in await owned_in_project(db, project_id):
            await _withdraw_seats(db, project_id, instance.id)
    return await get_own_agents(project_id, db, resolver)


async def _withdraw_seats(db, project_id: uuid.UUID, instance_id: uuid.UUID) -> None:
    await TopicMemberService(db).revoke_project_seats(
        project_id=project_id, member_handle=agent_instance_handle(instance_id)
    )


@router.post(
    "/{project_id}/own-agents",
    dependencies=[ROUTE_DECIDES],
    **typed_response(OwnAgentsOut),
)
async def add_own_agent(
    project_id: uuid.UUID,
    body: OwnAgentAdd,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Bring the caller's own Claude Code, with one of their model connections,
    into the project. Only a person adds their own, and only one of their own
    connections, of a kind the project allows; adding one already here
    changes nothing."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    if actor.user_id is None:
        raise ForbiddenError(say("ownAgentAddPersonOnly"))
    project = await ProjectService(db).get_or_404(project_id)
    await add(db, project, actor.user_id, body.connection_id, CLAUDE_CODE)
    await db.commit()
    return await get_own_agents(project_id, db, resolver)


@router.delete(
    "/{project_id}/own-agents/{handle}",
    dependencies=[ROUTE_DECIDES],
    **typed_response(OwnAgentsOut),
)
async def remove_own_agent(
    project_id: uuid.UUID,
    handle: str,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Take one of the caller's own agents out of the project: off its roster,
    out of its channels and out of reach of ``@``. Only its owner removes it."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    owned = await owned_by_seat(db, project_id, handle)
    if owned is None:
        raise NotFoundError(say("ownAgentNotFound"))
    if actor.user_id is None or actor.user_id != owned.owner_user_id:
        raise ForbiddenError(say("ownAgentRemoveOwnerOnly"))
    await remove(db, owned.instance_id)
    await _withdraw_seats(db, project_id, owned.instance_id)
    await db.commit()
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
