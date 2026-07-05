"""Integration tests for project grants (capability delegation)."""

import pytest
from anyio.from_thread import BlockingPortal
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError
from app.domain.grant.repositories import ProjectGrantRepository
from app.domain.grant.services import ProjectGrantService

PROJECT = 93001


class TestProjectGrant:
    @pytest.fixture(autouse=True)
    def setup(self, db_session: AsyncSession, _portal: BlockingPortal):
        self.db = db_session
        self.portal = _portal
        self.svc = ProjectGrantService(ProjectGrantRepository(db_session))

    def test_grant_is_idempotent(self):
        async def _run():
            g1 = await self.svc.grant(
                project_id=PROJECT,
                granted_by_user_id=1,
                resource_type="task",
                action="read",
                resource_id=42,
            )
            g2 = await self.svc.grant(
                project_id=PROJECT,
                granted_by_user_id=1,
                resource_type="task",
                action="read",
                resource_id=42,
            )
            return g1, g2

        g1, g2 = self.portal.call(_run)
        assert g1.id == g2.id

    def test_covering_matches_specific_and_typewide(self):
        async def _run():
            # user 1 shares one specific task; user 2 shares all tasks type-wide.
            await self.svc.grant(
                project_id=PROJECT,
                granted_by_user_id=1,
                resource_type="task",
                action="read",
                resource_id=42,
            )
            await self.svc.grant(
                project_id=PROJECT,
                granted_by_user_id=2,
                resource_type="task",
                action="read",
                resource_id=None,
            )
            covering_42 = await self.svc.granters_covering(
                project_id=PROJECT, resource_type="task", action="read", resource_id=42
            )
            covering_99 = await self.svc.granters_covering(
                project_id=PROJECT, resource_type="task", action="read", resource_id=99
            )
            return covering_42, covering_99

        covering_42, covering_99 = self.portal.call(_run)
        # task 42: both the specific (user 1) and type-wide (user 2) cover it.
        assert set(covering_42) == {1, 2}
        # task 99: only the type-wide grant (user 2) covers it.
        assert covering_99 == [2]

    def test_revoke_removes_coverage(self):
        async def _run():
            g = await self.svc.grant(
                project_id=PROJECT,
                granted_by_user_id=3,
                resource_type="knowledge",
                action="update",
            )
            await self.svc.revoke(grant_id=g.id, by_user_id=3)
            return await self.svc.granters_covering(
                project_id=PROJECT, resource_type="knowledge", action="update", resource_id=7
            )

        covering = self.portal.call(_run)
        assert covering == []

    def test_only_granter_can_revoke(self):
        async def _run():
            g = await self.svc.grant(
                project_id=PROJECT,
                granted_by_user_id=5,
                resource_type="task",
                action="update",
            )
            await self.svc.revoke(grant_id=g.id, by_user_id=6)

        with pytest.raises(ForbiddenError):
            self.portal.call(_run)
