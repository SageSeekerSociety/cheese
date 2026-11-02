"""What a request has read of a project's agent instances, left for the rest
of it (`app.core.request_facts`)."""

import uuid
from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_facts import remember
from app.domain.agent_instance.models import AgentInstance
from app.domain.identity.handles import agent_instance_handle
from app.domain.project.models import Project

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


def project_agents_ride_along(session: AsyncSession):
    """For `usage.payers.project_with_payer`: the project's instances read in
    the same statement, one row each, and left as `remember_project_agents`
    leaves them. Admission asks which of them the credential's seat is next
    (`AgentInstanceService.for_seat_handle`)."""

    def extend(stmt: Select):
        def record(project: Project, extras: list[tuple]) -> None:
            remember_project_agents(
                session, project.id, [e[0] for e in extras if e[0] is not None]
            )

        return (
            stmt.add_columns(AgentInstance)
            .outerjoin(AgentInstance, AgentInstance.project_id == Project.id)
            .order_by(AgentInstance.created_at),
            record,
        )

    return extend
