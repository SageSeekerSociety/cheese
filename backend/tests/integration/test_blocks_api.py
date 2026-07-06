"""Contract tests for the human-facing block/document read routes."""

from datetime import UTC, datetime

import pytest
from anyio.from_thread import BlockingPortal
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.services import BlockService
from app.domain.project.repositories import ProjectRepository
from tests.integration.conftest import CreatedUser


class TestBlocksApi:
    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        self.client = api_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.db = db_session
        self.portal = _portal

    def _seed_document(self, leader_id: int):
        async def _run():
            now = datetime.now(UTC)
            project = await ProjectRepository(self.db).create_project(
                name="p",
                description="d",
                color_code="#ffffff",
                team_id=1,
                leader_id=leader_id,
                start_date=now,
                end_date=now,
            )
            blocks = BlockService(BlockRepository(self.db))
            root = await blocks.create_block(
                project_id=project.id, content="Title", author_id=1, kind=BlockKind.DOCUMENT
            )
            await blocks.create_block(
                project_id=project.id,
                content="Section",
                author_id=1,
                kind=BlockKind.DOCUMENT,
                struct_parent_id=root.id,
            )
            return project.id, root.id

        return self.portal.call(_run)

    def test_get_block_and_document_as_member(self):
        _, root_id = self._seed_document(self.user.user_id)

        resp = self.client.get(f"/blocks/{root_id}", headers=self.headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["block"]["content"] == "Title"

        resp = self.client.get(f"/blocks/{root_id}/document", headers=self.headers)
        assert resp.status_code == 200
        doc = resp.json()["data"]["document"]
        assert doc["block"]["content"] == "Title"
        assert [c["block"]["content"] for c in doc["children"]] == ["Section"]

    def test_non_member_forbidden(self):
        _, root_id = self._seed_document(self.user.user_id + 9_999_999)
        resp = self.client.get(f"/blocks/{root_id}", headers=self.headers)
        assert resp.status_code == 403
