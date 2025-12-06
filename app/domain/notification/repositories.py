from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import Select, and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notification.models import Notification, NotificationType


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id_for_user(self, user_id: int, notification_id: int) -> Notification | None:
        stmt: Select[tuple[Notification]] = select(Notification).where(
            and_(
                Notification.id == notification_id,
                Notification.receiver_id == user_id,
                Notification.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: int,
        *,
        limit: int,
        cursor_created_at: datetime | None = None,
        cursor_id: int | None = None,
        type_: NotificationType | None = None,
        read: bool | None = None,
        ) -> Sequence[Notification]:
        stmt: Select[tuple[Notification]] = select(Notification).where(
            Notification.receiver_id == user_id,
            Notification.deleted_at.is_(None),
            Notification.finalized.is_(True),
        )

        if type_ is not None:
            stmt = stmt.where(Notification.type == type_)
        if read is not None:
            stmt = stmt.where(Notification.read == read)

        # Cursor-based pagination: order by created_at DESC, id DESC.
        if cursor_created_at is not None and cursor_id is not None:
            stmt = stmt.where(
                (Notification.created_at < cursor_created_at)
                | (
                    (Notification.created_at == cursor_created_at)
                    & (Notification.id < cursor_id)
                )
            )

        stmt = stmt.order_by(
            Notification.created_at.desc(),
            Notification.id.desc(),
        ).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def mark_all_as_read_for_user(self, user_id: int) -> int:
        stmt = (
            update(Notification)
            .where(
                and_(
                    Notification.receiver_id == user_id,
                    Notification.read.is_(False),
                )
            )
            .values(read=True)
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)

    async def count_unread_for_user(self, user_id: int) -> int:
        """Count unread notifications for a given user."""
        stmt = select(func.count(Notification.id)).where(
            Notification.receiver_id == user_id,
            Notification.read.is_(False),
            Notification.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def set_read_status_for_user(
        self, user_id: int, notification_id: int, read: bool
    ) -> int:
        stmt = (
            update(Notification)
            .where(
                and_(
                    Notification.receiver_id == user_id,
                    Notification.id == notification_id,
                )
            )
            .values(read=read)
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)

    async def find_all_by_ids_for_user(
        self, user_id: int, ids: Sequence[int]
    ) -> list[Notification]:
        if not ids:
            return []
        stmt: Select[tuple[Notification]] = select(Notification).where(
            Notification.receiver_id == user_id,
            Notification.id.in_(list(ids)),
            Notification.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_user(
        self,
        user_id: int,
        *,
        type_: NotificationType | None = None,
        read: bool | None = None,
    ) -> int:
        """Count notifications for a given user with optional filters.

        Mirrors the filters used in ``list_for_user`` so that pagination
        metadata (total/hasMore/nextStart) can be computed consistently.
        """
        stmt = select(func.count(Notification.id)).where(
            Notification.receiver_id == user_id,
            Notification.deleted_at.is_(None),
            Notification.finalized.is_(True),
        )

        if type_ is not None:
            stmt = stmt.where(Notification.type == type_)
        if read is not None:
            stmt = stmt.where(Notification.read == read)

        result = await self._session.execute(stmt)
        # ``scalar_one`` is safe here because COUNT always returns a row.
        return int(result.scalar_one() or 0)

    async def save_all(self, notifications: Sequence[Notification]) -> None:
        for n in notifications:
            self._session.add(n)
        await self._session.flush()

    async def soft_delete_for_user(self, user_id: int, notification_id: int) -> bool:
        notification = await self.get_by_id_for_user(user_id=user_id, notification_id=notification_id)
        if notification is None:
            return False
        notification.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self._session.flush()
        return True

    async def find_active_aggregation(
        self,
        *,
        recipient_id: int,
        aggregation_key: str,
        now: datetime,
    ) -> Notification | None:
        stmt: Select[tuple[Notification]] = (
            select(Notification)
            .where(
                Notification.receiver_id == recipient_id,
                Notification.aggregation_key == aggregation_key,
                Notification.is_aggregatable.is_(True),
                Notification.finalized.is_(False),
                Notification.aggregate_until.is_not(None),
                Notification.aggregate_until > now,
                Notification.deleted_at.is_(None),
            )
            .order_by(Notification.id.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_expired_aggregations(self, now: datetime) -> list[Notification]:
        stmt: Select[tuple[Notification]] = (
            select(Notification)
            .where(
                Notification.is_aggregatable.is_(True),
                Notification.finalized.is_(False),
                Notification.aggregate_until.is_not(None),
                Notification.aggregate_until <= now,
                Notification.deleted_at.is_(None),
            )
            .order_by(Notification.aggregate_until.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
