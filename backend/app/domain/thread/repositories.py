"""Data access for the thread (chat group) domain.

Three repositories — thread identity, membership, and the membership-application
approval workflow. Data-access only: no business logic, no authorization.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.thread.models import (
    ApplicationStatus,
    ApplicationType,
    Thread,
    ThreadKind,
    ThreadMemberRole,
    ThreadMembership,
    ThreadMembershipApplication,
)


class ThreadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        title: str | None,
        created_by: int,
        kind: ThreadKind = ThreadKind.GENERAL,
        project_id: int | None = None,
    ) -> Thread:
        now = datetime.now(UTC)
        thread = Thread(
            project_id=project_id,
            kind=kind,
            title=title,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self._session.add(thread)
        await self._session.flush()
        return thread

    async def get(self, thread_id: int) -> Thread | None:
        return (
            await self._session.execute(
                select(Thread).where(Thread.id == thread_id, Thread.deleted_at.is_(None))
            )
        ).scalar_one_or_none()

    async def rename(self, thread: Thread, title: str) -> Thread:
        thread.title = title
        thread.updated_at = datetime.now(UTC)
        await self._session.flush()
        return thread

    async def soft_delete(self, thread: Thread) -> None:
        """Dissolve a thread — soft-delete only the thread row (callers dissolve its
        memberships and pending applications separately)."""
        now = datetime.now(UTC)
        thread.deleted_at = now
        thread.updated_at = now
        await self._session.flush()

    async def touch(self, thread_id: int) -> None:
        thread = await self.get(thread_id)
        if thread is not None:
            thread.updated_at = datetime.now(UTC)
            await self._session.flush()

    async def threads_for_user(self, user_id: int) -> list[Thread]:
        """Threads the user is a (non-deleted) member of, most-recent activity first."""
        rows = (
            await self._session.execute(
                select(Thread)
                .join(ThreadMembership, ThreadMembership.thread_id == Thread.id)
                .where(
                    ThreadMembership.user_id == user_id,
                    ThreadMembership.deleted_at.is_(None),
                    Thread.deleted_at.is_(None),
                )
                .order_by(Thread.updated_at.desc())
            )
        ).scalars()
        return list(rows)


class ThreadMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self, thread_id: int, user_id: int, role: ThreadMemberRole = ThreadMemberRole.MEMBER
    ) -> ThreadMembership:
        # Revive a soft-deleted membership rather than duplicating it.
        existing = (
            await self._session.execute(
                select(ThreadMembership).where(
                    ThreadMembership.thread_id == thread_id,
                    ThreadMembership.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if existing is not None:
            existing.deleted_at = None
            existing.role = role
            existing.updated_at = now
            await self._session.flush()
            return existing
        membership = ThreadMembership(
            thread_id=thread_id,
            user_id=user_id,
            role=role,
            created_at=now,
            updated_at=now,
        )
        self._session.add(membership)
        await self._session.flush()
        return membership

    async def remove(self, thread_id: int, user_id: int) -> bool:
        membership = await self.get(thread_id, user_id)
        if membership is None:
            return False
        membership.deleted_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def get(self, thread_id: int, user_id: int) -> ThreadMembership | None:
        return (
            await self._session.execute(
                select(ThreadMembership).where(
                    ThreadMembership.thread_id == thread_id,
                    ThreadMembership.user_id == user_id,
                    ThreadMembership.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def members(self, thread_id: int) -> list[ThreadMembership]:
        rows = (
            await self._session.execute(
                select(ThreadMembership)
                .where(
                    ThreadMembership.thread_id == thread_id,
                    ThreadMembership.deleted_at.is_(None),
                )
                .order_by(ThreadMembership.id)
            )
        ).scalars()
        return list(rows)

    async def member_count(self, thread_id: int) -> int:
        return int(
            (
                await self._session.execute(
                    select(func.count(ThreadMembership.id)).where(
                        ThreadMembership.thread_id == thread_id,
                        ThreadMembership.deleted_at.is_(None),
                    )
                )
            ).scalar_one()
            or 0
        )

    async def role_of(self, thread_id: int, user_id: int) -> int | None:
        membership = await self.get(thread_id, user_id)
        return membership.role if membership is not None else None

    async def is_member(self, thread_id: int, user_id: int) -> bool:
        return await self.role_of(thread_id, user_id) is not None

    async def update_role(
        self, thread_id: int, user_id: int, role: ThreadMemberRole
    ) -> ThreadMembership | None:
        membership = await self.get(thread_id, user_id)
        if membership is None:
            return None
        membership.role = role
        membership.updated_at = datetime.now(UTC)
        await self._session.flush()
        return membership

    async def remove_all(self, thread_id: int) -> None:
        """Soft-delete every membership of a thread (used when dissolving)."""
        now = datetime.now(UTC)
        for membership in await self.members(thread_id):
            membership.deleted_at = now
        await self._session.flush()

    async def set_attention(
        self, thread_id: int, user_id: int, value: str | None
    ) -> ThreadMembership | None:
        membership = await self.get(thread_id, user_id)
        if membership is None:
            return None
        membership.attention_policy_override = value
        membership.updated_at = datetime.now(UTC)
        await self._session.flush()
        return membership

    async def set_read_watermark(
        self, thread_id: int, user_id: int, last_read_block_id: int
    ) -> int | None:
        """Advance a member's read high-water mark to ``last_read_block_id`` (monotonic —
        never moves it backwards). Returns the resulting watermark, or None if not a member."""
        membership = await self.get(thread_id, user_id)
        if membership is None:
            return None
        current = membership.last_read_block_id or 0
        if last_read_block_id > current:
            membership.last_read_block_id = last_read_block_id
            membership.updated_at = datetime.now(UTC)
            await self._session.flush()
        return membership.last_read_block_id or 0


class ThreadApplicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        thread_id: int,
        user_id: int,
        initiator_id: int,
        approver_id: int | None,
        type_: ApplicationType,
        role: ThreadMemberRole = ThreadMemberRole.MEMBER,
        message: str | None = None,
    ) -> ThreadMembershipApplication:
        now = datetime.now(UTC)
        app = ThreadMembershipApplication(
            thread_id=thread_id,
            user_id=user_id,
            initiator_id=initiator_id,
            approver_id=approver_id,
            type=type_.value,
            status=ApplicationStatus.PENDING.value,
            role=role,
            message=message,
            created_at=now,
            updated_at=now,
        )
        self._session.add(app)
        await self._session.flush()
        return app

    async def get(self, app_id: int) -> ThreadMembershipApplication | None:
        return (
            await self._session.execute(
                select(ThreadMembershipApplication).where(
                    ThreadMembershipApplication.id == app_id,
                    ThreadMembershipApplication.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def pending_for_thread(self, thread_id: int) -> list[ThreadMembershipApplication]:
        """Every PENDING invite/request targeting this thread (owner/admin view)."""
        rows = (
            await self._session.execute(
                select(ThreadMembershipApplication)
                .where(
                    ThreadMembershipApplication.thread_id == thread_id,
                    ThreadMembershipApplication.status == ApplicationStatus.PENDING.value,
                    ThreadMembershipApplication.deleted_at.is_(None),
                )
                .order_by(ThreadMembershipApplication.id.desc())
            )
        ).scalars()
        return list(rows)

    async def cancel_pending_for_thread(self, thread_id: int, *, processed_by: int) -> None:
        """Cancel all still-pending applications of a thread (used when dissolving)."""
        now = datetime.now(UTC)
        for app in await self.pending_for_thread(thread_id):
            app.status = ApplicationStatus.CANCELED.value
            app.processed_by_id = processed_by
            app.processed_at = now
            app.updated_at = now
        await self._session.flush()

    async def pending_for_approver(self, user_id: int) -> list[ThreadMembershipApplication]:
        rows = (
            await self._session.execute(
                select(ThreadMembershipApplication)
                .where(
                    ThreadMembershipApplication.approver_id == user_id,
                    ThreadMembershipApplication.status == ApplicationStatus.PENDING.value,
                    ThreadMembershipApplication.deleted_at.is_(None),
                )
                .order_by(ThreadMembershipApplication.id.desc())
            )
        ).scalars()
        return list(rows)

    async def set_status(
        self,
        app: ThreadMembershipApplication,
        status: ApplicationStatus,
        *,
        processed_by: int,
    ) -> ThreadMembershipApplication:
        now = datetime.now(UTC)
        app.status = status.value
        app.processed_by_id = processed_by
        app.processed_at = now
        app.updated_at = now
        await self._session.flush()
        return app
