"""Whether a personal agent's model connection is usable in a project."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_instance.own.ownership import (
    Owned,
    connection_of,
    owned_instance,
    owner_of,
)
from app.domain.agent_instance.own.settings import (
    allowed_kinds,
    allows_own_agents,
    host_refused,
    kinds_refused_sentence,
)


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


async def why_not(
    db: AsyncSession, instance_id: uuid.UUID, project_settings: dict[str, Any] | None
) -> str | None:
    """Existing project and connection restrictions, independent of the speaker."""
    owned = await owned_instance(db, instance_id)
    if owned is None:
        return None
    if not allows_own_agents(project_settings):
        return say("ownAgentsClosed")
    return await unusable(db, owned, project_settings)
