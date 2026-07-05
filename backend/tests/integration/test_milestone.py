"""Integration tests for the milestone domain (DB-backed)."""

from datetime import UTC, datetime, timedelta

import pytest
from anyio.from_thread import BlockingPortal
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.domain.milestone.models import MilestoneKind, MilestoneStatus
from app.domain.milestone.repositories import MilestoneRepository
from app.domain.milestone.services import MilestoneService

PROJECT = 96001


class TestMilestone:
    @pytest.fixture(autouse=True)
    def setup(self, db_session: AsyncSession, _portal: BlockingPortal):
        self.db = db_session
        self.portal = _portal
        self.svc = MilestoneService(MilestoneRepository(db_session))

    def test_create_and_order_by_due(self):
        base = datetime(2030, 1, 1, tzinfo=UTC)

        async def _run():
            await self.svc.create_milestone(
                project_id=PROJECT, title="late", due_at=base + timedelta(days=10), created_by_id=1
            )
            await self.svc.create_milestone(
                project_id=PROJECT,
                title="soon",
                due_at=base + timedelta(days=1),
                created_by_id=1,
                kind=MilestoneKind.PROTOCOL,
            )
            return await self.svc.list_by_project(PROJECT)

        listed = self.portal.call(_run)
        assert [m.title for m in listed] == ["soon", "late"]
        assert listed[0].kind == MilestoneKind.PROTOCOL.value

    def test_title_required(self):
        async def _run():
            await self.svc.create_milestone(
                project_id=PROJECT, title="  ", due_at=datetime.now(UTC), created_by_id=1
            )

        with pytest.raises(BadRequestError):
            self.portal.call(_run)

    def test_overdue_excludes_done(self):
        base = datetime(2030, 6, 1, tzinfo=UTC)

        async def _run():
            past = await self.svc.create_milestone(
                project_id=PROJECT, title="past", due_at=base - timedelta(days=1), created_by_id=1
            )
            done = await self.svc.create_milestone(
                project_id=PROJECT,
                title="past-done",
                due_at=base - timedelta(days=2),
                created_by_id=1,
            )
            await self.svc.mark_done(done.id)
            await self.svc.create_milestone(
                project_id=PROJECT, title="future", due_at=base + timedelta(days=5), created_by_id=1
            )
            overdue = await self.svc.list_overdue(PROJECT, now=base)
            return past, overdue

        past, overdue = self.portal.call(_run)
        assert [m.id for m in overdue] == [past.id]

    def test_mark_done(self):
        async def _run():
            m = await self.svc.create_milestone(
                project_id=PROJECT, title="x", due_at=datetime.now(UTC), created_by_id=1
            )
            return await self.svc.mark_done(m.id)

        done = self.portal.call(_run)
        assert done.status == MilestoneStatus.DONE.value
