"""收件箱的数据访问 —— 一张表，两种查法。

按 `receiver_id` 查的是知是那一侧的站内信（全站一个收件箱，翻页按游标）；按
`project_id` + `recipient_handle` 查的是某个项目里的收件箱（角标、等你决定、
话题相关性）。两种查法读的是同一张表的同一批行。
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notification.models import (
    Notification,
    NotificationLevel,
    NotificationType,
)

#: 分级限流 (spec §8.5): 每个房间每天最多 2 条 light、每周 1 条 strong。
_QUOTA = {
    NotificationLevel.light: (timedelta(days=1), 2),
    NotificationLevel.strong: (timedelta(days=7), 1),
}


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id_for_user(
        self, user_id: int, notification_id: int
    ) -> Notification | None:
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
                    Notification.deleted_at.is_(None),
                )
            )
            .values(read=True)
        )
        result = await self._session.execute(stmt)
        # UPDATE returns a CursorResult which has rowcount at runtime.
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

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
                    Notification.deleted_at.is_(None),
                )
            )
            .values(read=read)
        )
        result = await self._session.execute(stmt)
        # UPDATE returns a CursorResult which has rowcount at runtime.
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

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
        notification = await self.get_by_id_for_user(
            user_id=user_id, notification_id=notification_id
        )
        if notification is None:
            return False
        notification.deleted_at = datetime.now(UTC)
        await self._session.flush()
        return True

    # --- 项目收件箱 ------------------------------------------------------
    #
    # 下面这几条按 `project_id` + `recipient_handle` 查。一条通知只对一个人，所以
    # 「我看得见哪些」就是一句相等 —— 并表之前这里每一处都还得带上「或者谁的名都
    # 没点」那一档（广播），四处查询一处内存过滤，漏掉任何一处就是把别人的信念给
    # 了他。广播现在在写入时展开成一人一行。

    async def over_quota(
        self, topic_id: uuid.UUID | None, level: NotificationLevel
    ) -> bool:
        """这个房间这一档在窗口里是不是已经发满了（silent 不限，房间外的也不限）。"""
        if topic_id is None or level not in _QUOTA:
            return False
        window, cap = _QUOTA[level]
        count = await self._session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.topic_id == topic_id,
                Notification.level == level.value,
                Notification.created_at >= datetime.now(UTC) - window,
            )
        )
        return (count or 0) >= cap

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        recipient_handle: str,
        receiver_id: int | None,
        level: NotificationLevel,
        type_: NotificationType,
        title: str,
        body: str = "",
        topic_id: uuid.UUID | None = None,
        payload: dict | None = None,
    ) -> Notification:
        now = datetime.now(UTC)
        row = Notification(
            project_id=project_id,
            topic_id=topic_id,
            recipient_handle=recipient_handle,
            receiver_id=receiver_id,
            level=level.value,
            type=type_,
            title=title,
            body=body,
            metadata_payload=payload if payload is not None else {},
            read=False,
            is_aggregatable=False,
            finalized=True,
            version=0,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get(self, notification_id: int) -> Notification | None:
        return await self._session.get(Notification, notification_id)

    async def save(self, notification: Notification) -> Notification:
        await self._session.flush()
        await self._session.refresh(notification)
        return notification

    def _mine_in(self, stmt, project_id: uuid.UUID, recipient_handle: str | None):
        stmt = stmt.where(Notification.project_id == project_id)
        if recipient_handle is not None:
            stmt = stmt.where(Notification.recipient_handle == recipient_handle)
        return stmt

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        recipient_handle: str | None = None,
        unread_only: bool = False,
    ) -> list[Notification]:
        stmt = self._mine_in(select(Notification), project_id, recipient_handle)
        if unread_only:
            stmt = stmt.where(Notification.read.is_(False))
        stmt = stmt.order_by(Notification.created_at.desc())
        return list((await self._session.scalars(stmt)).all())

    async def list_inbox(
        self,
        project_id: uuid.UUID,
        *,
        recipient_handle: str | None = None,
    ) -> list[Notification]:
        """等你处理的事 (spec G2): 决策请求挂到拍板为止，验收卡挂到读过为止。"""
        stmt = self._mine_in(select(Notification), project_id, recipient_handle).where(
            or_(
                and_(
                    Notification.type == NotificationType.DECISION_REQUEST.value,
                    Notification.resolved_at.is_(None),
                ),
                and_(
                    Notification.type == NotificationType.ACCEPT_REQUEST.value,
                    Notification.read.is_(False),
                ),
            )
        )
        stmt = stmt.order_by(Notification.created_at.desc())
        return list((await self._session.scalars(stmt)).all())

    async def unread_count_in_project(
        self, project_id: uuid.UUID, *, recipient_handle: str | None = None
    ) -> int:
        """角标：这个项目里还没读、又不是 silent 的那些（silent 默默记下来）。"""
        stmt = self._mine_in(
            select(func.count()).select_from(Notification), project_id, recipient_handle
        ).where(
            Notification.read.is_(False),
            Notification.level != NotificationLevel.silent.value,
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def mark_all_read_in_project(
        self, project_id: uuid.UUID, *, recipient_handle: str | None = None
    ) -> int:
        """全部标记已读 —— 返回标了几条。没拍板的决策请求照样留在收件箱里。"""
        stmt = self._mine_in(select(Notification), project_id, recipient_handle).where(
            Notification.read.is_(False)
        )
        items = list((await self._session.scalars(stmt)).all())
        for row in items:
            row.read = True
        await self._session.flush()
        return len(items)

    async def mention_topic_ids(
        self, topic_ids: list[uuid.UUID], recipient_handle: str
    ) -> dict[uuid.UUID, bool]:
        """{topic_id: 这里 @ 他的那些还有没有未读} —— 一次查完。

        「被 @ 过」不用翻消息正文：一个 @ 落地的时候就在这里写了一行，房间和收件
        人两列都有索引。
        """
        if not topic_ids:
            return {}
        stmt = (
            select(Notification.topic_id, func.bool_or(Notification.read.is_(False)))
            .where(
                Notification.topic_id.in_(topic_ids),
                Notification.type == NotificationType.MENTION.value,
                Notification.recipient_handle == recipient_handle,
            )
            .group_by(Notification.topic_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {topic_id: bool(unread) for topic_id, unread in rows if topic_id}
