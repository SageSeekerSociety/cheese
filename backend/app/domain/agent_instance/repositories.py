"""Agent instance data access."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.models import AgentInstance


class AgentInstanceRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        handle: str,
        type_name: str | None,
        display_name: str,
    ) -> AgentInstance:
        instance = AgentInstance(
            project_id=project_id,
            handle=handle,
            type_name=type_name,
            display_name=display_name,
        )
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def get(self, instance_id: uuid.UUID) -> AgentInstance | None:
        return await self._session.get(AgentInstance, instance_id)

    async def get_by_handle(
        self, *, project_id: uuid.UUID, handle: str
    ) -> AgentInstance | None:
        result = await self._session.execute(
            select(AgentInstance).where(
                AgentInstance.project_id == project_id,
                AgentInstance.handle == handle,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_project(self, project_id: uuid.UUID) -> list[AgentInstance]:
        result = await self._session.execute(
            select(AgentInstance)
            .where(AgentInstance.project_id == project_id)
            .order_by(AgentInstance.created_at)
        )
        return list(result.scalars())

    async def delete(self, instance: AgentInstance) -> None:
        await self._session.delete(instance)
        await self._session.flush()
