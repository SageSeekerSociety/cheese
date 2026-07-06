"""Contract-level tests for the tool-RPC surface (/agent/tools) via TestClient.

Mints an agent_session token, calls the endpoint, and verifies the real block
write + the auth/gate behaviour end to end.
"""

from datetime import UTC, datetime

import pytest
from anyio.from_thread import BlockingPortal
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.authorization.authorizer import ProjectActor
from app.agent.authorization.token import mint_agent_session
from app.domain.block.models import AuthorKind, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.models import ProjectAiMode
from app.domain.project.repositories import ProjectRepository
from app.domain.thread.repositories import ThreadRepository


class TestAgentToolsApi:
    @pytest.fixture(autouse=True)
    def setup(
        self, api_client: TestClient, db_session: AsyncSession, _portal: BlockingPortal
    ):
        self.client = api_client
        self.db = db_session
        self.portal = _portal

    def _seed_project(self, ai_mode: ProjectAiMode):
        async def _run():
            repo = ProjectRepository(self.db)
            now = datetime.now(UTC)
            project = await repo.create_project(
                name="p",
                description="d",
                color_code="#ffffff",
                team_id=1,
                leader_id=1,
                start_date=now,
                end_date=now,
            )
            await repo.set_ai_config(project, ai_mode=ai_mode)
            thread = await ThreadRepository(self.db).create(
                project_id=project.id, created_by_id=1
            )
            return project.id, thread.id

        return self.portal.call(_run)

    def test_call_post_message_creates_block(self):
        project_id, thread_id = self._seed_project(ProjectAiMode.ASSISTED)
        token = mint_agent_session(
            ProjectActor(kind="agent", actor_id=55, project_id=project_id)
        )
        resp = self.client.post(
            "/agent/tools/call",
            headers={"X-Agent-Session": token},
            json={"tool": "post_message", "args": {"thread_id": thread_id, "content": "hi"}},
        )
        assert resp.status_code == 200
        block_id = resp.json()["data"]["result"]["block_id"]

        async def _read():
            return await BlockRepository(self.db).get_by_id(block_id)

        block = self.portal.call(_read)
        assert block is not None
        assert block.project_id == project_id
        assert block.author_id == 55
        assert block.author_kind == AuthorKind.AGENT.value
        assert block.kind == BlockKind.MESSAGE.value

    def test_schema_lists_tools(self):
        project_id, _ = self._seed_project(ProjectAiMode.ASSISTED)
        token = mint_agent_session(
            ProjectActor(kind="agent", actor_id=1, project_id=project_id)
        )
        resp = self.client.get("/agent/tools/schema", headers={"X-Agent-Session": token})
        assert resp.status_code == 200
        names = {t["name"] for t in resp.json()["data"]["tools"]}
        assert {"post_message", "write_document"} <= names

    def test_missing_token_rejected(self):
        resp = self.client.post(
            "/agent/tools/call", json={"tool": "post_message", "args": {}}
        )
        assert resp.status_code == 401

    def test_wrong_typed_arg_returns_400_not_500(self):
        project_id, _ = self._seed_project(ProjectAiMode.ASSISTED)
        token = mint_agent_session(
            ProjectActor(kind="agent", actor_id=55, project_id=project_id)
        )
        # thread_id must be an integer; a string must be rejected cleanly (400),
        # never reach asyncpg as a 500.
        resp = self.client.post(
            "/agent/tools/call",
            headers={"X-Agent-Session": token},
            json={"tool": "post_message", "args": {"thread_id": "abc", "content": "hi"}},
        )
        assert resp.status_code == 400

    def test_null_content_returns_400_not_500(self):
        project_id, thread_id = self._seed_project(ProjectAiMode.ASSISTED)
        token = mint_agent_session(
            ProjectActor(kind="agent", actor_id=55, project_id=project_id)
        )
        resp = self.client.post(
            "/agent/tools/call",
            headers={"X-Agent-Session": token},
            json={"tool": "post_message", "args": {"thread_id": thread_id, "content": None}},
        )
        assert resp.status_code == 400

    def test_agent_gate_blocks_when_ai_off(self):
        project_id, thread_id = self._seed_project(ProjectAiMode.OFF)
        token = mint_agent_session(
            ProjectActor(kind="agent", actor_id=55, project_id=project_id)
        )
        resp = self.client.post(
            "/agent/tools/call",
            headers={"X-Agent-Session": token},
            json={"tool": "post_message", "args": {"thread_id": thread_id, "content": "hi"}},
        )
        assert resp.status_code == 403
