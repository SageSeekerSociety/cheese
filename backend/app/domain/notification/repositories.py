"""Notification data access."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notification.models import Notification, NotifKind, NotifLevel

# 分级限流 (spec §8.5): per topic, at most 2 light/day and 1 strong/week.
_QUOTA = {
    NotifLevel.light: (timedelta(days=1), 2),
    NotifLevel.strong: (timedelta(days=7), 1),
}


class NotificationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def over_quota(
        self, topic_id: uuid.UUID | None, level: NotifLevel
    ) -> bool:
        """True when this topic already hit its quota for this level in the
        window (silent is never throttled; non-topic notifications either)."""
        if topic_id is None or level not in _QUOTA:
            return False
        window, cap = _QUOTA[level]
        count = await self._session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.topic_id == topic_id,
                Notification.level == level,
                Notification.created_at >= datetime.now(UTC) - window,
            )
        )
        return (count or 0) >= cap

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        level: NotifLevel,
        kind: NotifKind,
        title: str,
        body: str = "",
        target_handle: str | None = None,
        topic_id: uuid.UUID | None = None,
        payload: dict | None = None,
    ) -> Notification:
        notification = Notification(
            project_id=project_id,
            level=level,
            kind=kind,
            title=title,
            body=body,
            target_handle=target_handle,
            topic_id=topic_id,
            payload=payload if payload is not None else {},
        )
        self._session.add(notification)
        await self._session.flush()
        await self._session.refresh(notification)
        return notification

    async def get(self, notification_id: uuid.UUID) -> Notification | None:
        return await self._session.get(Notification, notification_id)

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        target_handle: str | None = None,
        unread_only: bool = False,
    ) -> list[Notification]:
        stmt = select(Notification).where(Notification.project_id == project_id)
        if target_handle is not None:
            # A user sees notifications addressed to them AND broadcasts
            # (target_handle IS NULL), which are meant for everyone.
            stmt = stmt.where(
                (Notification.target_handle == target_handle)
                | (Notification.target_handle.is_(None))
            )
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        stmt = stmt.order_by(Notification.created_at.desc())
        return list((await self._session.scalars(stmt)).all())

    async def list_inbox(
        self,
        project_id: uuid.UUID,
        *,
        target_handle: str | None = None,
    ) -> list[Notification]:
        """等你处理的事 (spec G2): decision requests until 拍板 (resolved), and
        accept requests until read, newest-first."""
        stmt = (
            select(Notification)
            .where(Notification.project_id == project_id)
            .where(
                or_(
                    and_(
                        Notification.kind == NotifKind.decision_request,
                        Notification.resolved_at.is_(None),
                    ),
                    and_(
                        Notification.kind == NotifKind.accept_request,
                        Notification.read_at.is_(None),
                    ),
                )
            )
        )
        if target_handle is not None:
            stmt = stmt.where(
                (Notification.target_handle == target_handle)
                | (Notification.target_handle.is_(None))
            )
        stmt = stmt.order_by(Notification.created_at.desc())
        return list((await self._session.scalars(stmt)).all())

    async def save(self, notification: Notification) -> Notification:
        await self._session.flush()
        await self._session.refresh(notification)
        return notification

    def _visible_to(self, stmt, target_handle: str | None):
        if target_handle is not None:
            stmt = stmt.where(
                (Notification.target_handle == target_handle)
                | (Notification.target_handle.is_(None))
            )
        return stmt

    async def unread_count(
        self, project_id: uuid.UUID, *, target_handle: str | None = None
    ) -> int:
        """Badge count: unread, non-silent notifications visible to this user.
        Silent ones are 默默记下来 (spec §8.6) — they never light the badge."""
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.project_id == project_id,
                Notification.read_at.is_(None),
                Notification.level != NotifLevel.silent,
            )
        )
        stmt = self._visible_to(stmt, target_handle)
        return int((await self._session.scalar(stmt)) or 0)

    async def mark_all_read(
        self, project_id: uuid.UUID, *, target_handle: str | None = None
    ) -> int:
        """全部标记已读 — returns how many were marked. Unresolved decision
        requests stay in the inbox (resolution ≠ read), but their badge count
        clears like Feishu."""
        stmt = select(Notification).where(
            Notification.project_id == project_id,
            Notification.read_at.is_(None),
        )
        stmt = self._visible_to(stmt, target_handle)
        items = list((await self._session.scalars(stmt)).all())
        now = datetime.now(UTC)
        for n in items:
            n.read_at = now
        await self._session.flush()
        return len(items)
