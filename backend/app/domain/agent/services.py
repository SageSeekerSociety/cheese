"""Business logic for the agent registry."""

from app.core.errors import BadRequestError, NotFoundError
from app.domain.agent.models import AdapterKind, Agent, AgentStatus
from app.domain.agent.repositories import AgentRepository


class AgentService:
    def __init__(self, repo: AgentRepository) -> None:
        self._repo = repo

    async def _require_agent(self, agent_id: int) -> Agent:
        agent = await self._repo.get_by_id(agent_id)
        if agent is None:
            raise NotFoundError(f"Agent {agent_id} not found")
        return agent

    async def create_agent(
        self,
        *,
        project_id: int,
        name: str,
        role_prompt: str = "",
        adapter_kind: AdapterKind = AdapterKind.CHEESED_CODEX,
        parent_agent_id: int | None = None,
    ) -> Agent:
        if not name.strip():
            raise BadRequestError("agent name is required")
        # A clone (分身) must share its 本体's project — an agent cannot span
        # projects (the aggregate-root invariant).
        if parent_agent_id is not None:
            parent = await self._require_agent(parent_agent_id)
            if parent.project_id != project_id:
                raise BadRequestError("parent agent belongs to another project")
        return await self._repo.create(
            project_id=project_id,
            name=name,
            role_prompt=role_prompt,
            adapter_kind=adapter_kind,
            parent_agent_id=parent_agent_id,
        )

    async def get_agent(self, agent_id: int) -> Agent:
        return await self._require_agent(agent_id)

    async def list_by_project(self, project_id: int) -> list[Agent]:
        return await self._repo.list_by_project(project_id)

    async def set_status(self, agent_id: int, status: AgentStatus) -> Agent:
        agent = await self._require_agent(agent_id)
        return await self._repo.set_status(agent, status)
