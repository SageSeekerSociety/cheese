"""投递：先记下来，再发出去，发到了回写一笔（结论 58）。

## 为什么记与发是同一个模块

`deliver()` 是全仓唯一一处发通知的入口，它的入参是寻址结果（`Addressed`），不是一
句文案 —— 谁该收到已经由 `delivery/addressing.py` 那个纯函数答完了，这里只负责让那
个答案真的到达。

记录和发送不分成两个模块，因为它们是同一个事务边界：账本那一行必须和引发它的事件
一起提交，否则「这条事件本该通知谁」这句话在崩溃之后就没人记得。分成 `ledger.py` 加
一个 `send.py`，就等于把同一个事务的两半放进两个文件，而它们之间那一步顺序正是这件
事的全部难点。

## 两个崩溃点

一次投递有两处可以断在中间，两处的救法不一样：

**写入之后、发出之前。** 账本那一行已经和事件一起提交了，通知还没发。这一档靠补发
救：`resend_unsent()` 扫 `sent_at IS NULL` 的行，重新发一遍。这也是渠道发送失败时
走的那条路 —— 发送抛异常不往上抛，那一行就留在「没发出去」，由补发接手。以前这一
档是静默丢失：`NotificationEventHandler.dispatch` 吞掉异常，而没有任何一行记着它本
该发出去。

**发出之后、回写之前。** 通知已经写进收件箱，`sent_at` 还没写回就崩了。这一档不能
靠账本自己认出来（账本里它还是「没发出去」），靠的是**去重键落在收件箱那一行上**：
补发再发一次，插入撞上 `notification.delivery_key` 的唯一约束，什么也不发生，然后
把 `sent_at` 补上。所以「恰好一次」是由去重键保证的，不是由「先发还是先回写」的顺序
保证的 —— 两种顺序都有一个崩得掉的窗口。

## 补发只重投站内信这一个渠道

第一次发送交给全部渠道；补发只交给站内信。因为账本的「恰好一次」只有站内信这一个
渠道担得起 —— 去重键落在 `notification.delivery_key` 的唯一约束上，补发插第二遍什
么也不发生。邮件与浏览器推送是往 Redis 队列里 rpush，队列里没有这个键，补发一次就
是真的多一封信、多一条推送；而一行始终发不出去的投递每分钟被扫一次，那就成了一台
定时发信机。

队列那两个渠道也不需要账本替它们重投：它们各自的 drain 有 claim/ack、`max_retries`
和死信队列，排进队列之后的送达由它们自己负责到底。代价说清楚：进程在记账之后、发
送之前整个没了（那一批渠道一个都没跑过），补发只救得回站内信那一条，这一次的邮件
和推送不会再补。站内信是收件人一定看得到的那一份，另外两个是它的扩音器。

## 试到第几次为止

`MAX_ATTEMPTS` 次。到顶还没发出去的行留在账本里、`sent_at` 仍是 NULL、`attempts`
到顶，补发不再扫它 —— 那一行就是死信：查得到、能人工看，但不会再每分钟重试一遍。

## 去重键跟着事件走

键是 `事件 id:收件人 handle`。跟着**事件**，不跟着这一次发送尝试：重试、补发、同一
条事件被算两遍，算出来都是同一个键，所以每个人只收到一次。

以前的键是 `事件类型:收件人集合:payload 的 sha1`，跟着**内容**走。差别在两头都真实
发生：payload 里多一个时间戳，同一件事就变成两条通知；两件不同的事凑巧同一份内容，
第二件就被吞掉。而且它住在一个带 TTL 的 Redis 键里，重启即失效。

## 名册按事件发生的时刻取

`record()` 当场把 handle 解析成收件箱并写进账本。补发是在事后发生的 —— 有可能是几
分钟后，也有可能是一次重启之后 —— 那时候再按 handle 查一遍，查到的是**那时候**的
名册。这条事件点的是事情发生时的那个人，所以名册在写入时定档，补发只照着账本发。

定档的是名册，不是时间戳：补发出去的那条通知按**入库的时刻**落 `created_at`，因为
收件箱是按 `created_at DESC` 翻页的，落一个旧时间戳会让这条通知出现在二十分钟前的
位置 —— 未读数加一，人打开收件箱却看不到新东西。事件发生的时刻留在账本的
`event_at` 上。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.domain.delivery.addressing import Addressed
from app.domain.delivery.models import Delivery
from app.domain.identity.arrival import Arrival, how_it_arrives
from app.domain.notification.handlers import (
    InAppNotificationHandler,
    NotificationDelivery,
    NotificationEventHandler,
)
from app.domain.notification.models import NotificationType
from app.domain.notification.publisher import build_notification_event_handler
from app.domain.user.services import user_by_handle

logger = logging.getLogger(__name__)

#: 补发试到第几次为止。见模块说明「试到第几次为止」。
MAX_ATTEMPTS: Final = 5


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DeliveryEvent:
    """一条要投出去的事件 —— 窄到只剩账本要记的部分。

    `id` 是这条事件的身份（房间里那条 block 的 id），去重键跟着它走，所以调用点重
    算一遍寻址不会变成第二次打扰。`occurred_at` 是事情发生的时刻，不是投递的时刻。
    """

    id: uuid.UUID
    type: NotificationType
    payload: dict
    occurred_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True, slots=True)
class Pending:
    """账本里一行还没送到的投递 —— 新记下的和补发扫出来的是同一种东西。"""

    id: uuid.UUID
    receiver_id: int
    type: NotificationType
    payload: dict
    dedup_key: str


def dedup_key(event_id: uuid.UUID, handle: str) -> str:
    """这条事件发给这个人的那一次投递的身份。跟事件走，不跟发送尝试走。"""
    return f"{event_id}:{handle}"


class Ledger:
    """投递账本。一个 session 一本，写的是调用方的事务。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._session = session
        self._now = now
        self._channels = build_notification_event_handler(session)
        #: 补发只走这一个渠道，见模块说明「补发只重投站内信这一个渠道」。
        self._mailbox_only = NotificationEventHandler(
            session=session,
            channel_handlers=[InAppNotificationHandler(session=session)],
        )

    async def deliver(self, event: DeliveryEvent, addressed: Addressed) -> None:
        """把这条事件送给它点到的那些人 —— 全仓唯一一处发通知。

        先记账再发送：中间任何一步没走完，账本里那一行都还在，补发会接着做完。
        """
        await self.send(await self.record(event, addressed), self._channels)

    async def record(self, event: DeliveryEvent, addressed: Addressed) -> list[Pending]:
        """把寻址结果落成账本上的行，返回这一次新记下的、还没发的那些。

        同一条事件记第二遍不会多出行来（去重键唯一，撞上就什么也不做），所以第二遍
        返回空列表：那些人第一遍已经被记下了，发不发由那些行自己说了算。

        agent 不在这里：它在自己房间的时间线上读到这条事件，往它的收件箱里塞一行写
        的是一条谁都不会打开的记录（`identity/arrival.py`）。handle 解析不到用户行
        也不记 —— `reporter_handle` 可能是外部提交的一个名字，少发一条通知好过账本
        里挂一行永远发不出去的投递。
        """
        recorded_at = self._now()
        pending: list[Pending] = []
        for recipient in addressed.recipients:
            if how_it_arrives(recipient.handle) is not Arrival.mailbox:
                continue
            user = await user_by_handle(self._session, recipient.handle)
            if user is None:
                continue
            key = dedup_key(event.id, recipient.handle)
            stmt = (
                pg_insert(Delivery)
                .values(
                    id=uuid.uuid4(),
                    event_id=event.id,
                    recipient_handle=recipient.handle,
                    receiver_id=user.id,
                    dedup_key=key,
                    type=event.type.value,
                    payload=event.payload,
                    event_at=event.occurred_at,
                    recorded_at=recorded_at,
                )
                .on_conflict_do_nothing(index_elements=["dedup_key"])
                .returning(Delivery.id)
            )
            row_id = (await self._session.execute(stmt)).scalar_one_or_none()
            if row_id is None:
                continue
            pending.append(
                Pending(
                    id=row_id,
                    receiver_id=user.id,
                    type=event.type,
                    payload=event.payload,
                    dedup_key=key,
                )
            )
        return pending

    async def send(
        self, pending: Sequence[Pending], channels: NotificationEventHandler
    ) -> None:
        """把这些行交给 `channels`，发到了就回写 `sent_at`，没发到就记一次尝试。

        一行发不出去不影响其余的，也不往上抛：账本里那一行留在「没发出去」，补发会
        再来。往上抛会连带回滚调用方的事务，而那个事务里装着引发这条投递的事件本
        身 —— 通知发不出去不是「这件事没发生」。
        """
        for row in pending:
            try:
                if not await self._send_one(row, channels):
                    await self._count_attempt(row)
            except Exception:
                logger.exception(
                    "投递没送出去，留给补发：delivery=%s key=%s", row.id, row.dedup_key
                )

    async def _send_one(self, row: Pending, channels: NotificationEventHandler) -> bool:
        if not await self._dispatch(row, channels):
            return False
        await self.mark_sent(row.id)
        return True

    async def _dispatch(self, row: Pending, channels: NotificationEventHandler) -> bool:
        """交给这一次该走的渠道。全都收下才算发出去。"""
        return await channels.dispatch(
            [
                NotificationDelivery(
                    recipient_id=row.receiver_id,
                    type=row.type,
                    payload=row.payload,
                    delivery_key=row.dedup_key,
                )
            ]
        )

    async def _count_attempt(self, row: Pending) -> None:
        """这一行又试了一次没成。到顶就不再试了 —— 那一行成了死信。"""
        record = await self._session.get(Delivery, row.id)
        if record is None:
            return
        record.attempts += 1
        await self._session.flush()
        if record.attempts >= MAX_ATTEMPTS:
            logger.error(
                "投递试满 %d 次仍未送出，不再补发：delivery=%s key=%s",
                MAX_ATTEMPTS,
                row.id,
                row.dedup_key,
            )

    async def mark_sent(self, delivery_id: uuid.UUID) -> None:
        """确认回写 —— 这一笔送到了，补发不必再管它。"""
        row = await self._session.get(Delivery, delivery_id)
        if row is not None and row.sent_at is None:
            row.sent_at = self._now()
            await self._session.flush()

    async def resend_unsent(self, *, limit: int = 200) -> int:
        """把账本里还没送出去的补发掉，返回这一轮补成了几条。

        名册不重算：发给账本里记着的那个收件箱。渠道也不全走一遍：只重投站内信，
        队列那两个渠道排进去之后由它们自己的 drain 负责到底。试满 `MAX_ATTEMPTS`
        次的那些行不再扫。
        """
        rows = (
            await self._session.scalars(
                select(Delivery)
                .where(Delivery.sent_at.is_(None))
                .where(Delivery.attempts < MAX_ATTEMPTS)
                .order_by(Delivery.recorded_at)
                .limit(limit)
            )
        ).all()
        await self.send(
            [
                Pending(
                    id=row.id,
                    receiver_id=row.receiver_id,
                    type=NotificationType(row.type),
                    payload=row.payload or {},
                    dedup_key=row.dedup_key,
                )
                for row in rows
            ],
            self._mailbox_only,
        )
        return sum(1 for row in rows if row.sent_at is not None)


async def deliver(
    session: AsyncSession, event: DeliveryEvent, addressed: Addressed
) -> None:
    """全仓唯一一处发通知。入参是寻址结果，不是一句文案（不变量 I11）。"""
    await Ledger(session).deliver(event, addressed)


async def resend_unsent_deliveries(sessions: SessionFactory) -> dict[str, int]:
    """定时补发 —— 账本上没有哪一行能自己发出去。

    这是「写入之后崩」那一档唯一的出路：那一行已经和事件一起提交了，发送这一半没
    人再碰它。不跑这个 job，账本就只是一份丢失记录，而不是一次补救。
    """
    async with sessions() as session:
        resent = await Ledger(session).resend_unsent()
        await session.commit()
    if resent:
        logger.info("补发了 %d 条没送出去的投递", resent)
    return {"resent": resent}
