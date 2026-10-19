"""Instantiate a team connection as a project teammate."""

import uuid

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
from app.domain.agent_instance.services import AgentInstanceService


async def add(
    db: AsyncSession,
    project,
    owner_user_id: int,
    connection_id: str,
    harness: str,
    *,
    model: str,
    device_id: str,
) -> AgentInstance:
    """Create one teammate with a team connection, model and execution device."""
    login = next(
        (c for c in await logins(db, project.team_id) if c.id == connection_id), None
    )
    if login is None:
        raise NotFoundError(say("ownAgentConnectionNotFound"))
    if model not in login.allowed_models:
        raise ForbiddenError(say("teamConnectionModelRefused"))
    account_id = uuid.UUID(login.id) if login.on_account else None
    nickname = await _nickname_of(db, owner_user_id)
    instance = AgentInstance(
        project_id=project.id,
        handle=own_handle(owner_user_id),
        type_name=None,
        configuration={"model": model, "execution_device_id": device_id},
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
            login_id=None if login.on_account else login.id,
            account_connection_id=account_id,
        )
    )
    instance.is_active = True
    await db.flush()
    await _name_owners_agents(db, project.id, owner_user_id)
    await AgentInstanceService(db).ensure_identity(instance)
    return instance
