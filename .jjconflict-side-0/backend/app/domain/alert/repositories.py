"""Alert data access."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.alert.models import Alert, AlertKind, AlertLevel

# 分级限流 (spec §8.5): per topic, at most 2 light/day and 1 strong/week.
_QUOTA = {
    AlertLevel.light: (timedelta(days=1), 2),
    AlertLevel.strong: (timedelta(days=7), 1),
}


class AlertRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def over_quota(self, topic_id: uuid.UUID | None, level: AlertLevel) -> bool:
        """True when this topic already hit its quota for this level in the
        window (silent is never throttled; non-topic notifications either)."""
        if topic_id is None or level not in _QUOTA:
            return False
        window, cap = _QUOTA[level]
        count = await self._session.scalar(
            select(func.count())
            .select_from(Alert)
            .where(
                Alert.topic_id == topic_id,
                Alert.level == level,
                Alert.created_at >= datetime.now(UTC) - window,
            )
        )
        return (count or 0) >= cap

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        level: AlertLevel,
        kind: AlertKind,
        title: str,
        body: str = "",
        target_handle: str | None = None,
        topic_id: uuid.UUID | None = None,
        payload: dict | None = None,
    ) -> Alert:
        notification = Alert(
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

    async def get(self, notification_id: uuid.UUID) -> Alert | None:
        return await self._session.get(Alert, notification_id)

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        target_handle: str | None = None,
        unread_only: bool = False,
    ) -> list[Alert]:
        stmt = select(Alert).where(Alert.project_id == project_id)
        if target_handle is not None:
            # A user sees notifications addressed to them AND broadcasts
            # (target_handle IS NULL), which are meant for everyone.
            stmt = stmt.where(
                (Alert.target_handle == target_handle) | (Alert.target_handle.is_(None))
            )
        if unread_only:
            stmt = stmt.where(Alert.read_at.is_(None))
        stmt = stmt.order_by(Alert.created_at.desc())
        return list((await self._session.scalars(stmt)).all())

    async def list_inbox(
        self,
        project_id: uuid.UUID,
        *,
        target_handle: str | None = None,
    ) -> list[Alert]:
        """等你处理的事 (spec G2): decision requests until 拍板 (resolved), and
        accept requests until read, newest-first."""
        stmt = (
            select(Alert)
            .where(Alert.project_id == project_id)
            .where(
                or_(
                    and_(
                        Alert.kind == AlertKind.decision_request,
                        Alert.resolved_at.is_(None),
                    ),
                    and_(
                        Alert.kind == AlertKind.accept_request,
                        Alert.read_at.is_(None),
                    ),
                )
            )
        )
        if target_handle is not None:
            stmt = stmt.where(
                (Alert.target_handle == target_handle) | (Alert.target_handle.is_(None))
            )
        stmt = stmt.order_by(Alert.created_at.desc())
        return list((await self._session.scalars(stmt)).all())

    async def save(self, notification: Alert) -> Alert:
        await self._session.flush()
        await self._session.refresh(notification)
        return notification

    def _visible_to(self, stmt, target_handle: str | None):
        if target_handle is not None:
            stmt = stmt.where(
                (Alert.target_handle == target_handle) | (Alert.target_handle.is_(None))
            )
        return stmt

    async def mention_topic_ids(
        self, topic_ids: list[uuid.UUID], target_handle: str
    ) -> dict[uuid.UUID, bool]:
        """{topic_id: is one of its @s at this user still unread} for every
        topic here that ever @'d them, in ONE query.

        This is why "被 @ 过" is answerable at all without reading message
        bodies: an @ writes a row HERE the moment it lands
        (``ChatService._notify_mentions``), keyed by topic and target, both
        indexed. Scanning ``blocks`` for the handle would be the slow way to
        learn something the notification already recorded.
        """
        if not topic_ids:
            return {}
        stmt = (
            select(Alert.topic_id, func.bool_or(Alert.read_at.is_(None)))
            .where(
                Alert.topic_id.in_(topic_ids),
                Alert.kind == AlertKind.mention,
                Alert.target_handle == target_handle,
            )
            .group_by(Alert.topic_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {topic_id: bool(unread) for topic_id, unread in rows if topic_id}

    async def unread_count(
        self, project_id: uuid.UUID, *, target_handle: str | None = None
    ) -> int:
        """Badge count: unread, non-silent notifications visible to this user.
        Silent ones are 默默记下来 (spec §8.6) — they never light the badge."""
        stmt = (
            select(func.count())
            .select_from(Alert)
            .where(
                Alert.project_id == project_id,
                Alert.read_at.is_(None),
                Alert.level != AlertLevel.silent,
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
        stmt = select(Alert).where(
            Alert.project_id == project_id,
            Alert.read_at.is_(None),
        )
        stmt = self._visible_to(stmt, target_handle)
        items = list((await self._session.scalars(stmt)).all())
        now = datetime.now(UTC)
        for n in items:
            n.read_at = now
        await self._session.flush()
        return len(items)
