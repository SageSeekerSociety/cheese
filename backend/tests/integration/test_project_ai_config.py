"""Integration tests for the project aggregate-root 2.0 extension."""

from datetime import UTC, datetime

import pytest
from anyio.from_thread import BlockingPortal
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import ProjectAiMode, ProjectApprovalPolicy
from app.domain.project.repositories import ProjectRepository


class TestProjectAiConfig:
    @pytest.fixture(autouse=True)
    def setup(self, db_session: AsyncSession, _portal: BlockingPortal):
        self.db = db_session
        self.portal = _portal
        self.repo = ProjectRepository(db_session)

    def _make_project(self):
        async def _run():
            now = datetime.now(UTC)
            return await self.repo.create_project(
                name="proj",
                description="d",
                color_code="#ffffff",
                team_id=1,
                leader_id=1,
                start_date=now,
                end_date=now,
            )

        return self.portal.call(_run)

    def test_defaults_ai_off(self):
        project = self._make_project()
        # Legacy/new projects are well-defined: AI off, manual approval, no root.
        assert project.ai_mode == ProjectAiMode.OFF.value
        assert project.approval_policy == ProjectApprovalPolicy.MANUAL.value
        assert project.root_thread_id is None

    def test_set_ai_config(self):
        project = self._make_project()

        async def _run():
            return await self.repo.set_ai_config(
                project,
                ai_mode=ProjectAiMode.ASSISTED,
                approval_policy=ProjectApprovalPolicy.THRESHOLD,
                root_thread_id=555,
            )

        updated = self.portal.call(_run)
        assert updated.ai_mode == ProjectAiMode.ASSISTED.value
        assert updated.approval_policy == ProjectApprovalPolicy.THRESHOLD.value
        assert updated.root_thread_id == 555
