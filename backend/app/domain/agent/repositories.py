"""Data access for the agent registry. No business logic."""

from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.models import AdapterKind, Agent, AgentStatus


class AgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        project_id: int,
        name: str,
        role_prompt: str = "",
        adapter_kind: AdapterKind = AdapterKind.CHEESED_CODEX,
        parent_agent_id: int | None = None,
    ) -> Agent:
        now = datetime.now(UTC)
        agent = Agent(
            project_id=project_id,
            name=name,
            role_prompt=role_prompt,
            adapter_kind=adapter_kind.value,
            status=AgentStatus.ACTIVE.value,
            parent_agent_id=parent_agent_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(agent)
        await self._session.flush()
        return agent

    async def get_by_id(self, agent_id: int) -> Agent | None:
        stmt: Select[tuple[Agent]] = select(Agent).where(
            Agent.id == agent_id, Agent.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_project(self, project_id: int) -> list[Agent]:
        stmt: Select[tuple[Agent]] = (
            select(Agent)
            .where(Agent.project_id == project_id, Agent.deleted_at.is_(None))
            .order_by(Agent.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def set_status(self, agent: Agent, status: AgentStatus) -> Agent:
        agent.status = status.value
        agent.updated_at = datetime.now(UTC)
        await self._session.flush()
        return agent
