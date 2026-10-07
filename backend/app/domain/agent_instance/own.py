"""A member's own coding agent, as a teammate in a project (#2991).

The row that makes a project's agent someone's (`OwnAgent`) is read in many
places — where its session runs, what it may be charged, who may call it — and
every one of them asks the same question: is the agent acting here somebody's
own, and whose. This is that one answer.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.models import AgentInstance, OwnAgent
from app.domain.identity.handles import agent_instance_handle


@dataclass(frozen=True)
class Owned:
    instance_id: uuid.UUID
    owner_user_id: int
    harness: str


async def owned_by_seat(
    db: AsyncSession, project_id: uuid.UUID, seat_handle: str | None
) -> Owned | None:
    """The owner of the agent that sits on rosters as ``seat_handle`` in this
    project, or None when that agent is not anyone's own (the project's 芝士,
    another teammate, a person)."""
    if not seat_handle:
        return None
    rows = await db.execute(
        select(AgentInstance.id, OwnAgent.owner_user_id, OwnAgent.harness)
        .join(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
        .where(AgentInstance.project_id == project_id)
    )
    for instance_id, owner_user_id, harness in rows:
        if agent_instance_handle(instance_id) == seat_handle:
            return Owned(instance_id, owner_user_id, harness)
    return None


async def owned_instance(db: AsyncSession, instance_id: uuid.UUID) -> Owned | None:
    row = await db.get(OwnAgent, instance_id)
    if row is None:
        return None
    return Owned(row.instance_id, row.owner_user_id, row.harness)
