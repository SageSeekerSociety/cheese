"""Whether a member's own agent may be used here, and by whom.

A member's own agent is not the project's: in a project closed to them it does
nothing, it answers to someone rather than to everyone, and its connection can
be gone or one the project does not allow. Every entry point asks these
questions in the same words — a task, a private chat, a mention, a session
start — and this module is where they are answered, with the sentence the room
is told when the answer is no.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.domain.agent_instance.models import AgentInstance, OwnCallers
from app.domain.agent_instance.own.ownership import (
    Owned,
    connection_of,
    owned_by_seat,
    owned_instance,
    owner_of,
)
from app.domain.agent_instance.own.settings import (
    allowed_kinds,
    allows_own_agents,
    host_refused,
    kinds_refused_sentence,
)
from app.domain.identity.handles import looks_like_agent_handle


async def unusable(
    db: AsyncSession, owned: Owned, project_settings: dict[str, Any] | None
) -> str | None:
    """Why a member's own agent cannot work here even for someone it answers
    to, as the sentence the room is told: its connection is gone, or is of a
    kind the project does not allow, or calls a model service at a host the
    project does not list. None when it can (it may still have to wait for its
    machine to come online)."""
    login = await connection_of(db, owned)
    if login is None:
        instance = await db.get(AgentInstance, owned.instance_id)
        owner = await owner_of(db, owned.instance_id)
        return say(
            "ownAgentConnectionGone",
            agent=(instance.display_name if instance else None) or "",
            owner=owner[1] if owner else "",
        )
    if login.kind not in allowed_kinds(project_settings):
        owner = await owner_of(db, owned.instance_id)
        return kinds_refused_sentence(owner[1] if owner else "", [login.kind])
    if host_refused(login, project_settings):
        owner = await owner_of(db, owned.instance_id)
        return say(
            "ownAgentHostNotAllowed",
            owner=owner[1] if owner else "",
            host=login.service_host or say("ownAgentKindModelService"),
        )
    return None


async def callable_by(
    db: AsyncSession, instance_id: uuid.UUID, caller: str | None
) -> bool:
    """Whether ``caller`` is someone this member's own agent answers to: its
    owner, and, when its owner opened it to the project, any person there.
    Never another agent. The callers already know ``caller`` may speak in the
    project; this answers only whose agent it is."""
    owned = await owned_instance(db, instance_id)
    if owned is None or not caller:
        return False
    owner = await owner_of(db, instance_id)
    if owner is not None and owner[0] == caller:
        return True
    return owned.callers is OwnCallers.project and not looks_like_agent_handle(caller)


async def why_not(
    db: AsyncSession,
    instance_id: uuid.UUID,
    caller: str,
    project_settings: dict[str, Any] | None,
) -> str | None:
    """Why ``caller`` may not set this agent to work, as the sentence the room
    is told; None when they may. A member's own agent is withheld in a project
    closed to them, from anyone it does not answer to, and when its connection
    is gone or one the project does not allow (``unusable``)."""
    owned = await owned_instance(db, instance_id)
    if owned is None:
        return None
    if not allows_own_agents(project_settings):
        return say("ownAgentsClosed")
    if not await callable_by(db, instance_id, caller):
        owner = await owner_of(db, instance_id)
        return say("ownAgentOwnerOnly", owner=owner[1] if owner is not None else "")
    return await unusable(db, owned, project_settings)


async def may_work_for(
    db: AsyncSession,
    project_id: uuid.UUID,
    seat_handle: str | None,
    person: str | None,
    project_settings: dict[str, Any] | None,
) -> bool:
    """Whether the agent on ``seat_handle`` may work a task ``person`` is
    responsible for: any project teammate may; a member's own agent only the
    tasks of someone it answers to, and only while the project lets members
    bring their own."""
    owned = await owned_by_seat(db, project_id, seat_handle)
    if owned is None:
        return True
    return allows_own_agents(project_settings) and await callable_by(
        db, owned.instance_id, person
    )


async def may_chat_with(
    db: AsyncSession,
    project_id: uuid.UUID,
    agent_handle: str | None,
    person: str,
    project_settings: dict[str, Any] | None,
) -> bool:
    """Whether ``person`` may open a private chat with the agent whose own
    handle is ``agent_handle``: with a member's own agent, only someone it
    answers to, in a project that lets members bring their own. Whether the
    kind of its owner's login is allowed is told in the chat, when a message
    calls it (``why_not``)."""
    if not agent_handle:
        return True
    instance_id = await db.scalar(
        select(AgentInstance.id).where(
            AgentInstance.project_id == project_id,
            AgentInstance.handle == agent_handle,
        )
    )
    if instance_id is None or await owned_instance(db, instance_id) is None:
        return True
    return allows_own_agents(project_settings) and await callable_by(
        db, instance_id, person
    )
