"""Admission of a team connection and its selected model."""

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


async def unusable(
    db: AsyncSession, owned: Owned, project_settings: dict[str, Any] | None
) -> str | None:
    """Refuse a missing connection or a model outside its team-defined list."""
    login = await connection_of(db, owned)
    if login is None:
        instance = await db.get(AgentInstance, owned.instance_id)
        owner = await owner_of(db, owned.instance_id)
        return say(
            "ownAgentConnectionGone",
            agent=(instance.display_name if instance else None) or "",
            owner=owner[1] if owner else "",
        )
    instance = await db.get(AgentInstance, owned.instance_id)
    model = (
        ((instance.configuration or {}).get("model") if instance else None)
        or login.model
        or "sonnet"
    )
    if not login.allowed_models or (
        model is not None and model not in login.allowed_models
    ):
        return say("teamConnectionModelRefused")
    return None


async def why_not(
    db: AsyncSession, instance_id: uuid.UUID, project_settings: dict[str, Any] | None
) -> str | None:
    """Existing project and connection restrictions, independent of the speaker."""
    owned = await owned_instance(db, instance_id)
    if owned is None:
        return None
    return await unusable(db, owned, project_settings)
