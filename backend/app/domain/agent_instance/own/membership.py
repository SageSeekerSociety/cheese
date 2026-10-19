"""Bringing a member's own agent into a project and removing it.

Only its owner adds or removes it. Adding requires a connection the project
allows; removal preserves its memory, as retiring a teammate does.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.agent_instance.models import (
    AgentInstance,
    NameSource,
    OwnAgent,
)
from app.domain.agent_instance.own.connections import label, logins
from app.domain.agent_instance.own.naming import (
    _name_owners_agents,
    own_handle,
    own_name,
)
from app.domain.agent_instance.own.ownership import _nickname_of
from app.domain.agent_instance.own.settings import (
    allowed_kinds,
    allows_own_agents,
    host_refused,
)
from app.domain.agent_instance.services import AgentInstanceService


async def add(
    db: AsyncSession, project, owner_user_id: int, connection_id: str, harness: str
) -> AgentInstance:
    """Its owner brings their own Claude Code with one of their connections
    (`Login.id`) into the project. One agent per connection: adding a
    connection already here is a no-op, and one its owner removed comes back
    with what it knew. ``harness`` is the registry's name for Claude Code,
    passed in because this package sits below the registry."""
    if not allows_own_agents(project.settings):
        raise ForbiddenError(say("ownAgentsClosedHere"))
    login = next(
        (c for c in await logins(db, owner_user_id) if c.id == connection_id),
        None,
    )
    if login is None:
        raise NotFoundError(say("ownAgentConnectionNotFound"))
    if login.kind not in allowed_kinds(project.settings):
        raise ForbiddenError(say("ownAgentConnectionKindRefused"))
    if host_refused(login, project.settings):
        raise ForbiddenError(
            say("ownAgentServiceHostRefused", host=login.service_host or "")
        )
    account_id = uuid.UUID(login.id) if login.on_account else None
    instance = await db.scalar(
        select(AgentInstance)
        .join(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
        .where(
            AgentInstance.project_id == project.id,
            OwnAgent.owner_user_id == owner_user_id,
            OwnAgent.account_connection_id == account_id
            if login.on_account
            else OwnAgent.device_id == login.device_id,
        )
    )
    if instance is None:
        nickname = await _nickname_of(db, owner_user_id)
        instance = AgentInstance(
            project_id=project.id,
            handle=own_handle(owner_user_id),
            type_name=None,
            configuration={},
            display_name=own_name(nickname, label(login)),
            name_source=NameSource.human,
        )
        db.add(instance)
        await db.flush()
        db.add(
            OwnAgent(
                instance_id=instance.id,
                owner_user_id=owner_user_id,
                harness=harness,
                device_id=login.device_id,
                account_connection_id=account_id,
            )
        )
    instance.is_active = True
    await db.flush()
    await _name_owners_agents(db, project.id, owner_user_id)
    await AgentInstanceService(db).ensure_identity(instance)
    return instance


async def remove(db: AsyncSession, instance_id: uuid.UUID) -> None:
    """Its owner takes it out of the project: off the roster and out of reach.
    The row stays, as a retired teammate's does, so its memory is there if they
    add the same connection again; its seats are its caller's to revoke."""
    instance = await db.get(AgentInstance, instance_id)
    if instance is not None:
        instance.is_active = False
        await db.flush()
