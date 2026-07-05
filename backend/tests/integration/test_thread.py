"""Integration tests for the thread domain (DB-backed)."""

import pytest
from anyio.from_thread import BlockingPortal
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.domain.thread.models import (
    AttentionPolicy,
    MemberKind,
    MemberRole,
    ThreadKind,
)
from app.domain.thread.repositories import ThreadRepository
from app.domain.thread.services import ThreadService

PROJECT_A = 91001
PROJECT_B = 91002


class TestThreadDomain:
    @pytest.fixture(autouse=True)
    def setup(self, db_session: AsyncSession, _portal: BlockingPortal):
        self.db = db_session
        self.portal = _portal
        self.svc = ThreadService(ThreadRepository(db_session))

    def test_nested_threads(self):
        async def _run():
            root = await self.svc.create_thread(
                project_id=PROJECT_A, created_by_id=1, title="root"
            )
            child = await self.svc.create_thread(
                project_id=PROJECT_A, created_by_id=1, title="child", parent_thread_id=root.id
            )
            children = await self.svc.list_children(root.id)
            return root, child, children

        root, child, children = self.portal.call(_run)
        assert child.parent_thread_id == root.id
        assert [t.id for t in children] == [child.id]

    def test_subthread_must_be_same_project(self):
        async def _run():
            root = await self.svc.create_thread(project_id=PROJECT_A, created_by_id=1)
            await self.svc.create_thread(
                project_id=PROJECT_B, created_by_id=1, parent_thread_id=root.id
            )

        with pytest.raises(BadRequestError):
            self.portal.call(_run)

    def test_membership_users_and_agents(self):
        async def _run():
            t = await self.svc.create_thread(
                project_id=PROJECT_A, created_by_id=1, kind=ThreadKind.MANAGEMENT
            )
            await self.svc.add_member(
                thread_id=t.id,
                member_id=1,
                member_kind=MemberKind.AGENT,
                role=MemberRole.SUPERIOR,
                attention_policy=AttentionPolicy.ALL_MESSAGES,
            )
            await self.svc.add_member(
                thread_id=t.id,
                member_id=2,
                member_kind=MemberKind.AGENT,
                role=MemberRole.SUBORDINATE,
                attention_policy=AttentionPolicy.MENTION_ONLY,
            )
            members = await self.svc.list_members(t.id)
            return members

        members = self.portal.call(_run)
        assert len(members) == 2
        superior = next(m for m in members if m.role == MemberRole.SUPERIOR.value)
        assert superior.attention_policy == AttentionPolicy.ALL_MESSAGES.value

    def test_duplicate_member_rejected(self):
        async def _run():
            t = await self.svc.create_thread(project_id=PROJECT_A, created_by_id=1)
            await self.svc.add_member(
                thread_id=t.id, member_id=7, member_kind=MemberKind.USER
            )
            await self.svc.add_member(
                thread_id=t.id, member_id=7, member_kind=MemberKind.USER
            )

        with pytest.raises(BadRequestError):
            self.portal.call(_run)

    def test_idle_window_requires_window(self):
        async def _run():
            t = await self.svc.create_thread(project_id=PROJECT_A, created_by_id=1)
            await self.svc.add_member(
                thread_id=t.id,
                member_id=3,
                member_kind=MemberKind.AGENT,
                attention_policy=AttentionPolicy.IDLE_WINDOW,
            )

        with pytest.raises(BadRequestError):
            self.portal.call(_run)
