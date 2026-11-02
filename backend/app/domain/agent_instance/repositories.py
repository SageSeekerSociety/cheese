"""Agent instance data access."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_facts import recall, remember
from app.domain.agent_instance.facts import (
    AGENT_INSTANCE_TABLES,
    remember_project_agents,
)
from app.domain.agent_instance.models import AgentInstance, NameSource
from app.domain.identity.handles import (
    agent_instance_id_bounds,
)

_AGENT_INSTANCE_TABLES = AGENT_INSTANCE_TABLES


class AgentInstanceRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def project_of_seat_handle(self, handle: str) -> uuid.UUID | None:
        bounds = agent_instance_id_bounds(handle)
        if bounds is None:
            return None
        low, high = bounds
        return await self._session.scalar(
            select(AgentInstance.project_id)
            .where(AgentInstance.id.between(low, high))
            .limit(1)
        )

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        handle: str,
        type_name: str | None,
        display_name: str,
        name_source: NameSource,
        configuration: dict,
    ) -> AgentInstance:
        instance = AgentInstance(
            project_id=project_id,
            handle=handle,
            type_name=type_name,
            display_name=display_name,
            name_source=name_source,
            configuration=configuration,
        )
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def get(self, instance_id: uuid.UUID) -> AgentInstance | None:
        if isinstance(self._session, AsyncSession):
            if self._session.autoflush:
                await self._session.flush()
            found, cached = recall(self._session, ("agent_instance_by_id", instance_id))
            if found:
                return cached
        instance = await self._session.get(AgentInstance, instance_id)
        if isinstance(self._session, AsyncSession):
            remember(
                self._session,
                ("agent_instance_by_id", instance_id),
                instance,
                tables=_AGENT_INSTANCE_TABLES,
            )
        return instance

    async def get_by_handle(
        self, *, project_id: uuid.UUID, handle: str
    ) -> AgentInstance | None:
        if isinstance(self._session, AsyncSession):
            if self._session.autoflush:
                await self._session.flush()
            found_new, is_new = recall(
                self._session, ("newly_created_project", project_id)
            )
            if found_new and is_new:
                return None
            found_all, cached_all = recall(
                self._session, ("project_agent_instances", project_id)
            )
            if found_all:
                return next((r for r in cached_all if r.handle == handle), None)
        result = await self._session.execute(
            select(AgentInstance).where(
                AgentInstance.project_id == project_id,
                AgentInstance.handle == handle,
            )
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[AgentInstance]:
        """Every agent in every project. For the boot-time identity backfill."""
        return list((await self._session.scalars(select(AgentInstance))).all())

    async def list_for_project(self, project_id: uuid.UUID) -> list[AgentInstance]:
        if isinstance(self._session, AsyncSession):
            if self._session.autoflush:
                await self._session.flush()
            found, cached = recall(
                self._session, ("project_agent_instances", project_id)
            )
            if found:
                return list(cached)
        result = await self._session.execute(
            select(AgentInstance)
            .where(AgentInstance.project_id == project_id)
            .order_by(AgentInstance.created_at)
        )
        rows = list(result.scalars())
        if isinstance(self._session, AsyncSession):
            remember_project_agents(self._session, project_id, rows)
        return rows
