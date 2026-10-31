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

from app.domain.conversation.services import of_rooms, room_column
from app.domain.notification.models import (
    Notification,
    NotificationLevel,
    NotificationType,
)

#: 叫人去处理一件事的那几种：芝士在等他回答，有人邀请他、申请加入他管的团队。它们
#: 等的那件事，「等你处理」按当下的状态现算一份（`api/awaiting_deps.py`）——没办完
#: 时在那里，办完就从那里消失。动态再列一遍，同一件事就在一页上出现两次，办完以后
#: 这边还留着一条说「等你」的旧话。所以它们只负责把人叫回来（推送、邮件、桌面通知），
#: 不进动态。
_CALLS_TO_ACT = (
    NotificationType.CHEESE_QUESTION,
    NotificationType.PROJECT_INVITE,
    NotificationType.PROJECT_TRANSFER,
    NotificationType.TEAM_INVITATION,
    NotificationType.TEAM_JOIN_REQUEST,
)


def _in_feed(stmt):
    """动态里有哪些：发生过的事，不包括叫人去处理一件事的那些（`_CALLS_TO_ACT`）。

    平台提示（`ROOM_NOTICE`）一个类别码走完所有事，分不出来的得看它说的是什么：带
    着验收卡号的（`payload.cardId`，`agent/announce.py` 的 `about_card`）是「待你
    审阅」「可以合并了」「不会自动合并」这几句等验收人动手的话，那张卡在「等你处理」
    里；其余的（任务几天没有进展、到点的提醒、部署失败）没有一处列着，留在动态。

    只管列表、条数、未读数和「全部标为已读」。推送和桌面通知不经过这里
    （`pushable_after`）：把人叫回来正是那几种的用处。
    """
    return stmt.where(
        Notification.type.not_in(_CALLS_TO_ACT),
        or_(
            Notification.type != NotificationType.ROOM_NOTICE,
            Notification.metadata_payload["cardId"].as_string().is_(None),
        ),
    )


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _my_mail(self, stmt, user_id: int):
        """知是那一侧的收件箱里有哪些信：收件人是我，而且不是项目收件箱的行。

        后半句由 `recipient_handle` 为空说出：项目那一侧写下的每一行都带着名册上
        的名字（`add`），投递账本写给知是的那些只有 `receiver_id`
        （`notification/handlers.py`）。两边同住一张表，读的时候各认各的那一列。

        少了这一句，项目收件箱的一行（`cheese_notify` 发的变更提醒、决策请求）会
        同时落进知是的铃铛，而那边渲染不了它：前端按 `type` 找模板，模板读的是
        `payload` 里的字段，项目通知的文字却在 `title`/`body` 上，渲染出来是一行
        读不出内容的空壳；未读数却照加，知是那边一点「全部已读」还会把项目角标
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
        read: bool | None = None,
    ) -> Sequence[Notification]:
        """动态的一页（`_in_feed`），新的在前。"""
        stmt: Select[tuple[Notification]] = _in_feed(
            self._my_mail(select(Notification), user_id)
        ).where(Notification.finalized.is_(True))

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

    async def pushable_after(
        self, user_id: int, types: frozenset[NotificationType], after: int, limit: int
    ) -> list[Notification]:
        """我名下这几种、还没读、比 `after` 新的信里最新的 `limit` 条，旧的在前。

        积压多了取最新的那几条，而不是最早的：人回来时要知道的是现在的事。
        """
        stmt = self._my_mail(select(Notification), user_id).where(
            Notification.type.in_(types),
            Notification.read.is_(False),
            Notification.id > after,
        )
        rows = await self._session.scalars(
            stmt.order_by(Notification.id.desc()).limit(limit)
        )
        return list(reversed(rows.all()))

    async def latest_id_for_user(
        self, user_id: int, types: frozenset[NotificationType] | None = None
    ) -> int | None:
        """The newest of `user_id`'s rows of these `types`; of any type without."""
        stmt = self._my_mail(select(func.max(Notification.id)), user_id)
        if types is not None:
            stmt = stmt.where(Notification.type.in_(types))
        return await self._session.scalar(stmt)

    async def mark_all_as_read_for_user(self, user_id: int) -> int:
        """动态里的全部标为已读。看不见的那几种（`_in_feed`）不动：它们的已读由那件
        事办完时结掉（`delivery/ledger.py` 的 `settle`），不由一下「全部已读」替人
        说成办完了。"""
        stmt = (
            _in_feed(self._my_mail(update(Notification), user_id))
            .where(Notification.read.is_(False))
            .values(read=True)
        )
        result = await self._session.execute(stmt)
        # UPDATE returns a CursorResult which has rowcount at runtime.
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def count_unread_for_user(self, user_id: int) -> int:
        """Count unread notifications for a given user.

        The same rows ``list_for_user`` lists: the inbox does not show a row that
        has not been finalized, so that row must not be counted either. A count
        computed over rows nobody can be shown leaves the badge lit with an empty
        inbox under it — and the button that would clear it (mark all read) is
        drawn only while the list itself holds an unread row, so nothing on the
        page could put it out. For the same reason it counts only what the feed
        holds (`_in_feed`).
        """
        stmt = _in_feed(
            self._my_mail(select(func.count(Notification.id)), user_id)
        ).where(
            Notification.finalized.is_(True),
            Notification.read.is_(False),
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
        read: bool | None = None,
    ) -> int:
        """Count notifications for a given user with optional filters.

        Mirrors the filters used in ``list_for_user`` so that pagination
        metadata (total/hasMore/nextStart) can be computed consistently.
        """
        stmt = _in_feed(
            self._my_mail(select(func.count(Notification.id)), user_id)
        ).where(Notification.finalized.is_(True))

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
        conversation_id: uuid.UUID | None = None,
        payload: dict | None = None,
        flush: bool = True,
    ) -> Notification:
        now = datetime.now(UTC)
        row = Notification(
            project_id=project_id,
            conversation_id=conversation_id,
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
        if flush:
            await self._session.flush()
        return row

    async def get(self, notification_id: int) -> Notification | None:
        return await self._session.get(Notification, notification_id)

    async def save(self, notification: Notification) -> Notification:
        await self._session.flush()
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
        limit: int,
        at_or_before: Notification | None = None,
    ) -> list[Notification]:
        """这个项目的收件箱里我看得见的那几页信，新的在前。

        `limit` 必填：这条读以前没有上界，一个项目跑久了它会一直长（一次把整个
        历史拉回来）。分页是键集，不是 offset —— 收件箱的头一直在长，一边翻一边
        有新信写进来，offset 会漏行也会重行。

        `at_or_before` 是下一页从这里开始的那一行（含它自己）。键是
        `(created_at, id)` 两列：`created_at` 会撞（同一秒写下的好几条，广播展开
        出来的一批正是这样），单靠它分不干净，`id` 补上第二列就有了全序。
        """
        stmt = self._mine_in(select(Notification), project_id, recipient_handle)
        if unread_only:
            stmt = stmt.where(Notification.read.is_(False))
        if at_or_before is not None:
            stmt = stmt.where(
                or_(
                    Notification.created_at < at_or_before.created_at,
                    and_(
                        Notification.created_at == at_or_before.created_at,
                        Notification.id <= at_or_before.id,
                    ),
                )
            )
        stmt = stmt.order_by(
            Notification.created_at.desc(), Notification.id.desc()
        ).limit(limit)
        return list((await self._session.scalars(stmt)).all())

    async def count_for_project(
        self,
        project_id: uuid.UUID,
        *,
        recipient_handle: str,
        unread_only: bool = False,
    ) -> int:
        """上面那条读的总数，和它同一套过滤条件。

        `list_for_project` 现在只回一页，所以 `total` 不能再拿这一页的条数充数 ——
        那正是分页之前的写法（`page(items, len(items))`），一加上限它就开始说谎。
        一条 `count(*)` 走的是同一个 `(project_id, recipient_handle)` 索引。
        """
        stmt = self._mine_in(
            select(func.count()).select_from(Notification),
            project_id,
            recipient_handle,
        )
        if unread_only:
            stmt = stmt.where(Notification.read.is_(False))
        return int((await self._session.scalar(stmt)) or 0)

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
        """全部标记已读 —— 返回标了几条。没拍板的决策请求照样留在收件箱里。

        一条 UPDATE 改完，不把命中的行读回来：返回值就是这条语句的 `rowcount`。
        `synchronize_session="fetch"` 在 PostgreSQL 上走 `UPDATE ... RETURNING`，
        一句话既改完又让会话里的对象跟上，不会先 SELECT 一遍。
        """
        stmt = (
            self._mine_in(update(Notification), project_id, recipient_handle)
            .where(Notification.read.is_(False))
            .values(read=True)
            .execution_options(synchronize_session="fetch")
        )
        result = await self._session.execute(stmt)
        # UPDATE returns a CursorResult which has rowcount at runtime.
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def still_open(
        self, project_ids: list[uuid.UUID], *, recipient_handle: str
    ) -> list[Notification]:
        """这几个项目里写给他、还没了结的通知：没拍板的决策请求和没读过的变更提醒，新的在前。

        「待办」从当下的事实重算，而这两件都是事实：决策请求 `resolved_at` 为空（读过
        不等于答过），变更提醒还没读。和 `list_inbox` 同一条判据，范围换成他能看见的
        全部项目；验收卡不在这里，等审的改动从任务本身算。
        """
        if not project_ids:
            return []
        stmt = (
            select(Notification)
            .where(
                Notification.project_id.in_(project_ids),
                Notification.recipient_handle == recipient_handle,
                Notification.deleted_at.is_(None),
                or_(
                    and_(
                        Notification.type == NotificationType.DECISION_REQUEST.value,
                        Notification.resolved_at.is_(None),
                    ),
                    and_(
                        Notification.type == NotificationType.CHANGE_ALERT.value,
                        Notification.read.is_(False),
                        or_(
                            Notification.level.is_(None),
                            Notification.level != NotificationLevel.silent.value,
                        ),
                    ),
                ),
            )
            .order_by(Notification.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def decision_topic_ids(
        self, topic_ids: list[uuid.UUID], recipient_handle: str
    ) -> dict[uuid.UUID, bool]:
        """{频道: 这里向他要的决策还有没有没拍板的} —— 一次查完。

        一个频道那一格认的是它全部的对话：它自己那条线、它的任务和它的支线。一条
        在任务里问的决策请求照样在它所在频道的角标上亮着（那条会话是频道的事），
        只是回执落在任务里而不是频道主线上。

        和验收卡同一个形状：**在不在 key 里**是「这房间找他拍过板」（拍完也还是
        他的事），**value** 是「现在就等他」。没拍板的判据和收件箱同一条：
        `resolved_at` 为空，读过不等于答过。
        """
        if not topic_ids:
            return {}
        room = room_column(Notification.conversation_id).label("room")
        stmt = (
            select(
                room,
                func.bool_or(Notification.resolved_at.is_(None)),
            )
            .where(
                of_rooms(Notification.conversation_id, topic_ids),
                Notification.type == NotificationType.DECISION_REQUEST.value,
                Notification.recipient_handle == recipient_handle,
                Notification.deleted_at.is_(None),
            )
            .group_by(room)
        )
        rows = (await self._session.execute(stmt)).all()
        return {topic_id: bool(open_) for topic_id, open_ in rows if topic_id}
