"""Data access for threads and memberships. No business logic."""

from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.thread.models import (
    AttentionPolicy,
    MemberKind,
    MemberRole,
    Thread,
    ThreadKind,
    ThreadMembership,
)


class ThreadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- threads ----------------------------------------------------------

    async def create(
        self,
        *,
        project_id: int,
        created_by_id: int,
        title: str = "",
        kind: ThreadKind = ThreadKind.GENERAL,
        parent_thread_id: int | None = None,
    ) -> Thread:
        now = datetime.now(UTC)
        thread = Thread(
            project_id=project_id,
            parent_thread_id=parent_thread_id,
            kind=kind.value,
            title=title,
            created_by_id=created_by_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(thread)
        await self._session.flush()
        return thread

    async def get_by_id(self, thread_id: int) -> Thread | None:
        stmt: Select[tuple[Thread]] = select(Thread).where(
            Thread.id == thread_id, Thread.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_children(self, parent_thread_id: int) -> list[Thread]:
        stmt: Select[tuple[Thread]] = (
            select(Thread)
            .where(Thread.parent_thread_id == parent_thread_id, Thread.deleted_at.is_(None))
            .order_by(Thread.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_project(self, project_id: int) -> list[Thread]:
        stmt: Select[tuple[Thread]] = (
            select(Thread)
            .where(Thread.project_id == project_id, Thread.deleted_at.is_(None))
            .order_by(Thread.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # --- memberships ------------------------------------------------------

    async def get_membership(
        self, *, thread_id: int, member_id: int, member_kind: MemberKind
    ) -> ThreadMembership | None:
        stmt: Select[tuple[ThreadMembership]] = select(ThreadMembership).where(
            ThreadMembership.thread_id == thread_id,
            ThreadMembership.member_id == member_id,
            ThreadMembership.member_kind == member_kind.value,
            ThreadMembership.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def add_membership(
        self,
        *,
        thread_id: int,
        member_id: int,
        member_kind: MemberKind,
        role: MemberRole = MemberRole.MEMBER,
        attention_policy: AttentionPolicy = AttentionPolicy.MENTION_ONLY,
        attention_window_seconds: int | None = None,
    ) -> ThreadMembership:
        now = datetime.now(UTC)
        membership = ThreadMembership(
            thread_id=thread_id,
            member_id=member_id,
            member_kind=member_kind.value,
            role=role.value,
            attention_policy=attention_policy.value,
            attention_window_seconds=attention_window_seconds,
            cursor_block_id=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(membership)
        await self._session.flush()
        return membership

    async def list_members(self, thread_id: int) -> list[ThreadMembership]:
        stmt: Select[tuple[ThreadMembership]] = (
            select(ThreadMembership)
            .where(
                ThreadMembership.thread_id == thread_id,
                ThreadMembership.deleted_at.is_(None),
            )
            .order_by(ThreadMembership.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def advance_cursor(
        self, membership: ThreadMembership, cursor_block_id: int
    ) -> ThreadMembership:
        membership.cursor_block_id = cursor_block_id
        membership.updated_at = datetime.now(UTC)
        await self._session.flush()
        return membership
