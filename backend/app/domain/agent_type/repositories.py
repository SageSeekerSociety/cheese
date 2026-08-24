"""Agent type data access."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_type.models import AgentType


class AgentTypeRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, **fields) -> AgentType:
        agent_type = AgentType(**fields)
        self._session.add(agent_type)
        await self._session.flush()
        return agent_type

    async def get_by_name(self, name: str) -> AgentType | None:
        result = await self._session.execute(
            select(AgentType).where(AgentType.name == name)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[AgentType]:
        result = await self._session.execute(
            select(AgentType).order_by(AgentType.created_at)
        )
        return list(result.scalars())

    async def delete(self, agent_type: AgentType) -> None:
        await self._session.delete(agent_type)
        await self._session.flush()
