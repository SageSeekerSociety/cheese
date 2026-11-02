"""What a request has read of a project's agent instances, left for the rest
of it (`app.core.request_facts`)."""

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_facts import remember
from app.domain.agent_instance.models import AgentInstance
from app.domain.identity.handles import agent_instance_handle

AGENT_INSTANCE_TABLES = frozenset({"agent_instances"})


def remember_project_agents(
    session: AsyncSession, project_id: uuid.UUID, rows: Sequence[AgentInstance]
) -> None:
    """Leave a project's instances, oldest first, for
    `AgentInstanceRepository.list_for_project` and the lookups by id and by
    seat handle to answer from."""
    remember(
        session,
        ("project_agent_instances", project_id),
        tuple(rows),
        tables=AGENT_INSTANCE_TABLES,
    )
    for inst in rows:
        remember(
            session,
            ("agent_instance_by_id", inst.id),
            inst,
            tables=AGENT_INSTANCE_TABLES,
        )
        remember(
            session,
            ("seat_project", agent_instance_handle(inst.id)),
            inst.project_id,
            tables=AGENT_INSTANCE_TABLES,
        )
