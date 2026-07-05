"""Integration tests for the agent registry (DB-backed)."""

import pytest
from anyio.from_thread import BlockingPortal
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.domain.agent.models import AdapterKind, AgentStatus
from app.domain.agent.repositories import AgentRepository
from app.domain.agent.services import AgentService

PROJECT_A = 92001
PROJECT_B = 92002


class TestAgentRegistry:
    @pytest.fixture(autouse=True)
    def setup(self, db_session: AsyncSession, _portal: BlockingPortal):
        self.db = db_session
        self.portal = _portal
        self.svc = AgentService(AgentRepository(db_session))

    def test_create_and_list(self):
        async def _run():
            a = await self.svc.create_agent(
                project_id=PROJECT_A, name="lead", role_prompt="you coordinate"
            )
            b = await self.svc.create_agent(
                project_id=PROJECT_A, name="worker", adapter_kind=AdapterKind.NAIVE_API
            )
            listed = await self.svc.list_by_project(PROJECT_A)
            return a, b, listed

        a, b, listed = self.portal.call(_run)
        assert a.status == AgentStatus.ACTIVE.value
        assert b.adapter_kind == AdapterKind.NAIVE_API.value
        assert {a.id, b.id} == {x.id for x in listed}

    def test_name_required(self):
        async def _run():
            await self.svc.create_agent(project_id=PROJECT_A, name="   ")

        with pytest.raises(BadRequestError):
            self.portal.call(_run)

    def test_clone_must_share_project(self):
        async def _run():
            primary = await self.svc.create_agent(project_id=PROJECT_A, name="primary")
            await self.svc.create_agent(
                project_id=PROJECT_B, name="clone", parent_agent_id=primary.id
            )

        with pytest.raises(BadRequestError):
            self.portal.call(_run)

    def test_status_transitions(self):
        async def _run():
            a = await self.svc.create_agent(project_id=PROJECT_A, name="a")
            paused = await self.svc.set_status(a.id, AgentStatus.PAUSED)
            return paused

        paused = self.portal.call(_run)
        assert paused.status == AgentStatus.PAUSED.value
