"""收件箱的数据访问 —— 一张表，两种查法。

按 `receiver_id` 查的是知是那一侧的站内信（全站一个收件箱，翻页按游标）；按
`project_id` + `recipient_handle` 查的是某个项目里的收件箱（角标、等你决定、
话题相关性）。两种查法读的是同一张表，但各认各的行：带着名册上名字的那些是项目
收件箱的，不带的才是站内信（`_my_mail` 与 `_mine_in` 各说一半）。
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notification.models import (
    Notification,
    NotificationLevel,
    NotificationType,
)


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _my_mail(self, stmt, user_id: int):
        """知是那一侧的收件箱里有哪些信：收件人是我，而且不是项目收件箱的行。

        后半句由 `recipient_handle` 为空说出：项目那一侧写下的每一行都带着名册上
        的名字（`add`），投递账本写给知是的那些只有 `receiver_id`
        （`notification/handlers.py`）。两边同住一张表，读的时候各认各的那一列。

        少了这一句，房间里的一次 @ 会同时落进知是的铃铛，而那边渲染不了它：前端
        按 `type` 找模板，`MENTION` 那一个读的是 `payload` 里的
        `mentioner`/`discussionTitle`/`discussionId`，项目通知一个都没有（文字在
        `title`/`body` 上），渲染出来是一句「有人提到了你 / 在讨论 未知讨论 中提到
        了你」，还不带跳转；未读数却照加，知是那边一点「全部已读」还会把项目角标
        一起清掉。

        按 id 点名的那几条（取一条、标一条、删一条）不带这一句：那是**已经拿着
        行号**的调用者在动自己名下的那一行，两侧都认它 —— 项目那一侧读
        `deleted_at`，所以这边删掉的不会还在角标里亮着。列表里出不来的东西，
        UI 也变不出行号来。
        """
        return stmt.where(
            Notification.receiver_id == user_id,
            Notification.recipient_handle.is_(None),
            Notification.deleted_at.is_(None),
        )

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
        stmt: Select[tuple[Notification]] = self._my_mail(
            select(Notification), user_id
        ).where(Notification.finalized.is_(True))

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
            self._my_mail(update(Notification), user_id)
            .where(Notification.read.is_(False))
            .values(read=True)
        )
        result = await self._session.execute(stmt)
        # UPDATE returns a CursorResult which has rowcount at runtime.
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def count_unread_for_user(self, user_id: int) -> int:
        """Count unread notifications for a given user."""
        stmt = self._my_mail(select(func.count(Notification.id)), user_id).where(
            Notification.read.is_(False)
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
        stmt = self._my_mail(select(func.count(Notification.id)), user_id).where(
            Notification.finalized.is_(True)
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
    # 下面这几条按 `project_id` + `recipient_handle` 查，收件人是必填的。一条通知
    # 只对一个人，所以「我看得见哪些」就是一句相等 —— 并表之前这里每一处都还得带
    # 上「或者谁的名都没点」那一档（广播），四处查询一处内存过滤，漏掉任何一处就
    # 是把别人的信念给了他。广播现在在写入时展开成一人一行。

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

    def _mine_in(self, stmt, project_id: uuid.UUID, recipient_handle: str):
        """这个项目里我看得见的那些 —— 收件人必填，没有「不给就全看」那一档。

        `deleted_at` 也挡在这里：一条通知现在两侧都读得到（站内信按
        `receiver_id`，项目收件箱按 handle），所以 `DELETE /notifications/{id}`
        软删掉的那一条不能还在项目角标和「等你决定」里亮着。
        """
        return stmt.where(
            Notification.project_id == project_id,
            Notification.recipient_handle == recipient_handle,
            Notification.deleted_at.is_(None),
        )

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        recipient_handle: str,
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
        recipient_handle: str,
    ) -> list[Notification]:
        """等你处理的事 (spec G2): 决策请求挂到拍板为止，验收卡挂到读过为止；
        变更提醒 (spec §8.5 的第一种典型通知) 也挂到读过为止。

        变更提醒本来一条都读不到（<#不带@的消息没有通知> 的事实 2），而
        `unread_count_in_project` 却把它数进项目角标 —— 于是角标亮着，人进去一条
        也读不到，只能靠「全部已读」把角标按掉。这里是那个角标的落地名单。

        `silent` 的不进来：它的意思就是「记下来，别打扰」，而它本来也不点亮角标。
        """
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
                and_(
                    Notification.type == NotificationType.CHANGE_ALERT.value,
                    Notification.read.is_(False),
                    or_(
                        Notification.level.is_(None),
                        Notification.level != NotificationLevel.silent.value,
                    ),
                ),
            )
        )
        stmt = stmt.order_by(Notification.created_at.desc())
        return list((await self._session.scalars(stmt)).all())

    async def unread_count_in_project(
        self, project_id: uuid.UUID, *, recipient_handle: str
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
        self, project_id: uuid.UUID, *, recipient_handle: str
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
                Notification.deleted_at.is_(None),
            )
            .group_by(Notification.topic_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {topic_id: bool(unread) for topic_id, unread in rows if topic_id}
