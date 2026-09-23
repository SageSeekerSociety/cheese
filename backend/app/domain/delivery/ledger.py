"""Record human delivery, mailbox rows and external-channel intent atomically.

The recipient user ID is snapshotted when the event occurs. Both mailbox rows
and email/push intent use stable event/recipient keys, so a partial batch can be
retried without duplicating intent. SMTP and push happen only after commit in
their own bounded consumers; provider acceptance can still be duplicated after
a lost response. Agent receipt state is managed by delivery.agent.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.domain.delivery.addressing import Addressed
from app.domain.delivery.models import Delivery, TimedDelivery
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

#: 社交事件 id 的命名空间。固定值：换掉它等于把在途的去重键全部作废，同一件事会
#: 再通知一遍。
_EVENT_NAMESPACE: Final = uuid.uuid5(uuid.NAMESPACE_DNS, "notification.cheese")


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
    #: **必填**。给它一个「现在」的默认值，等于让每个调用点都可以不声不响地把投递的
    #: 时刻当成事件的时刻，而这个字段整条设计都在说那两个不是一回事。
    occurred_at: datetime


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


def event_id_for(type_: NotificationType, record_id: int) -> uuid.UUID:
    """长在一条领域记录上的那种事件的身份：哪条记录，发生了哪件事。

    `record_id` 是引发它的那条记录（申请、邀请、讨论回复），`type_` 是那条记录上
    发生的这一件事 —— 两样都要：一条邀请从发出到被取消是同一条记录上的两件事，只
    按记录算，第二件就会撞上第一件的去重键，收件人再也收不到取消那一条。

    算出来的 id 只取决于这两样，所以同一件事重算一遍得到同一个 id：重试、补发、同
    一条事件被算两遍，收件人只被打扰一次。当场 `uuid4()` 得到的是**这一次调用**的
    身份，那就等于没有去重。
    """
    return uuid.uuid5(_EVENT_NAMESPACE, f"{type_.value}:{record_id}")


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

    async def deliver(self, event: DeliveryEvent, addressed: Addressed) -> None:
        """把这条事件送给它点到的那些人 —— 走账本的唯一入口。

        先记账再发送：中间任何一步没走完，账本里那一行都还在，补发会接着做完。

        一个人都没记下就不建渠道 —— 「谁也没点到」是常态（平台自己在处理的那些提示
        一条都不发），那种调用不该顺手拉起一整套渠道对象。
        """
        pending = await self.record(event, addressed)
        if not pending:
            return
        await self.send(pending, build_notification_event_handler(self._session))

    async def record(self, event: DeliveryEvent, addressed: Addressed) -> list[Pending]:
        """把寻址结果落成账本上的行，返回这一次新记下的、还没发的那些。

        同一条事件记第二遍不会多出行来（去重键唯一，撞上就什么也不做），所以第二遍
        返回空列表：那些人第一遍已经被记下了，发不发由那些行自己说了算。

        agent 不在这里：它在自己房间的时间线上读到这条事件，往它的收件箱里塞一行写
        的是一条谁都不会打开的记录（`identity/arrival.py`）。handle 解析不到用户行
        也不记 —— `reporter_handle` 可能是外部提交的一个名字，少发一条通知好过账本
        里挂一行永远发不出去的投递。
        """
        pending: list[Pending] = []
        for recipient in addressed.recipients:
            if how_it_arrives(recipient.handle) is not Arrival.mailbox:
                continue
            user = await user_by_handle(self._session, recipient.handle)
            if user is None:
                continue
            row = await self.record_mailbox(event, recipient.handle, user.id)
            if row is not None:
                pending.append(row)
        return pending

    async def record_mailbox(
        self, event: DeliveryEvent, handle: str, receiver_id: int
    ) -> Pending | None:
        """Record a resolved mailbox, including identities snapshotted by timers."""
        key = dedup_key(event.id, handle)
        row_id = (
            await self._session.execute(
                pg_insert(Delivery)
                .values(
                    id=uuid.uuid4(),
                    event_id=event.id,
                    recipient_handle=handle,
                    receiver_id=receiver_id,
                    dedup_key=key,
                    type=event.type.value,
                    payload=event.payload,
                    event_at=event.occurred_at,
                    recorded_at=self._now(),
                )
                .on_conflict_do_nothing(index_elements=["dedup_key"])
                .returning(Delivery.id)
            )
        ).scalar_one_or_none()
        if row_id is None:
            return None
        return Pending(row_id, receiver_id, event.type, event.payload, key)

    async def send(
        self, pending: Sequence[Pending], channels: NotificationEventHandler
    ) -> None:
        """把这些行交给 `channels`，发到了就回写 `sent_at`，没发到就记一次尝试。

        渠道那边出的事到不了这里：`dispatch()` 把每个渠道的异常接在各自的 savepoint
        里，只把「有没有全收下」返回回来。所以一行发不出去不影响其余的，也不往上
        抛 —— 那一行留在「没发出去」等补发。往上抛会连带回滚调用方的事务，而那个事
        务里装着引发这条投递的事件本身，通知发不出去不是「这件事没发生」。

        剩下会抛的只有账本自己那两笔 DB 写，它们照抛不接：能让 `flush()` 失败的东西
        已经把整个 session 的事务在 Postgres 里作废了，接住它只是把一个提交必败的
        session 交还给调用方，而这一行到底试了几次也没人记上。
        """
        for row in pending:
            if not await self._send_one(row, channels):
                await self._count_attempt(row)

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
            row.state = "received"
            await self._session.execute(
                update(TimedDelivery)
                .where(
                    TimedDelivery.event_id == row.event_id,
                    TimedDelivery.delivered_at.is_(None),
                )
                .values(delivered_at=row.sent_at)
            )
            await self._session.flush()

    async def resend_unsent(self, *, limit: int = 200) -> int:
        """Retry snapshotted recipients without duplicating committed intent.

        Legacy rows retain mailbox-only retry because their external copies
        already belong to the Redis migration drain.
        """
        rows = (
            await self._session.scalars(
                select(Delivery)
                .where(Delivery.sent_at.is_(None))
                .where(Delivery.receiver_id.is_not(None))
                .where(Delivery.attempts < MAX_ATTEMPTS)
                .order_by(Delivery.recorded_at)
                .limit(limit)
            )
        ).all()
        if not rows:
            return 0
        for external in (False, True):
            selected = [row for row in rows if row.external_channels == external]
            if not selected:
                continue
            channels = (
                build_notification_event_handler(self._session)
                if external
                else NotificationEventHandler(
                    session=self._session,
                    channel_handlers=[InAppNotificationHandler(session=self._session)],
                )
            )
            await self.send(
                [
                    Pending(
                        id=row.id,
                        receiver_id=row.receiver_id,
                        type=NotificationType(row.type),
                        payload=row.payload or {},
                        dedup_key=row.dedup_key,
                    )
                    for row in selected
                    if row.receiver_id is not None
                ],
                channels,
            )
        return sum(1 for row in rows if row.sent_at is not None)


async def deliver(
    session: AsyncSession, event: DeliveryEvent, addressed: Addressed
) -> None:
    """走账本的唯一入口。入参是寻址结果，不是一句文案。

    全仓发通知只有这一处（I11）：房间里的事件和社交那几条都从这里出去。
    """
    await Ledger(session).deliver(event, addressed)


async def resend_unsent_deliveries(sessions: SessionFactory) -> dict[str, int]:
    """定时补发 —— 账本上没有哪一行能自己发出去。

    这是第一档（渠道没收下）唯一的出路：那一行已经和事件一起提交了，而发送这一半
    没有任何人会再碰它。不跑这个 job，账本就只是一份丢失记录，而不是一次补救。
    """
    async with sessions() as session:
        resent = await Ledger(session).resend_unsent()
        await session.commit()
    if resent:
        logger.info("补发了 %d 条没送出去的投递", resent)
    return {"resent": resent}
