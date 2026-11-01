"""Record human delivery, mailbox rows and external-channel intent atomically.

The recipient user ID is snapshotted when the event occurs. Both mailbox rows
and email/push intent use stable event/recipient keys, so a partial batch can be
retried without duplicating intent. SMTP and push happen only after commit in
their own bounded consumers; provider acceptance can still be duplicated after
a lost response. Agent receipt state is managed by delivery.agent.

The other half — the notification code, the shape of one event, its stable id
and its dedup key — is data, and lives at layer 1 in
``app.domain.delivery_events`` so that every layer can say something happened
without climbing to this one. This module re-exports it, because whoever reads
the ledger usually wants both halves at once. Writing stays here: it lands
rows, sends in-app notifications and mail.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import Select, cast, delete, func, inspect, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from app.core.db import SessionFactory
from app.domain import happenings
from app.domain.addressing import Addressed
from app.domain.delivery.models import Delivery, TimedDelivery
from app.domain.delivery_events import (
    DeliveryEvent,
    NotificationType,
    dedup_key,
)
from app.domain.identity.arrival import Arrival, how_it_arrives
from app.domain.notification.handlers import (
    InAppNotificationHandler,
    NotificationDelivery,
    NotificationEventHandler,
)
from app.domain.notification.models import ChannelDelivery, Notification
from app.domain.notification.publisher import build_notification_event_handler
from app.domain.user.services import users_by_handle

logger = logging.getLogger(__name__)

#: 补发试到第几次为止。见模块说明「试到第几次为止」。
MAX_ATTEMPTS: Final = 5


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Pending:
    """账本里一行还没送到的投递 —— 新记下的和补发扫出来的是同一种东西。"""

    id: uuid.UUID
    receiver_id: int
    type: NotificationType
    payload: dict
    dedup_key: str


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
        handles = [
            recipient.handle
            for recipient in addressed.recipients
            if how_it_arrives(recipient.handle) is Arrival.mailbox
        ]
        users = await users_by_handle(self._session, handles)
        pending: list[Pending] = []
        for handle in handles:
            user = users.get(handle)
            if user is None:
                continue
            row = await self.record_mailbox(event, handle, user.id)
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
        await happenings.happened(
            self._session,
            happenings.NOTIFIED,
            user_id=receiver_id,
            payload={"type": event.type.value, "payload": event.payload},
        )
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
        if self._session.autoflush and (
            self._session.new or self._session.dirty or self._session.deleted
        ):
            await self._session.flush()
        now = self._now()
        event_id = (
            await self._session.execute(
                update(Delivery)
                .where(Delivery.id == delivery_id, Delivery.sent_at.is_(None))
                .values(sent_at=now, state="received")
                .returning(Delivery.event_id)
            )
        ).scalar_one_or_none()
        if event_id is not None:
            in_map = self._session.sync_session.identity_map.get(
                self._session.sync_session.identity_key(Delivery, (delivery_id,))
            )
            if in_map is not None and not inspect(in_map).expired_attributes:
                attributes.set_committed_value(in_map, "sent_at", now)
                attributes.set_committed_value(in_map, "state", "received")
            await self._session.execute(
                update(TimedDelivery)
                .where(
                    TimedDelivery.event_id == event_id,
                    TimedDelivery.delivered_at.is_(None),
                )
                .values(delivered_at=now)
            )

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


async def retract(session: AsyncSession, event_id: uuid.UUID) -> None:
    """这件事不作数了：它发出去的每一笔连同收件箱里那一行一起删掉。

    一条被删掉的公告不该还躺在谁的动态里，点开是一页找不到的东西。账本上的行也
    删：留着一行没发出去的投递，补发会把收件箱那一行再写回来。
    """
    keys = list(
        (
            await session.scalars(
                select(Delivery.dedup_key).where(Delivery.event_id == event_id)
            )
        ).all()
    )
    if not keys:
        return
    await session.execute(
        delete(Notification).where(Notification.delivery_key.in_(keys))
    )
    await session.execute(
        delete(ChannelDelivery).where(ChannelDelivery.delivery_key.in_(keys))
    )
    await session.execute(delete(Delivery).where(Delivery.event_id == event_id))
    await session.flush()


async def amend(session: AsyncSession, event_id: uuid.UUID, payload: dict) -> None:
    """这件事说的话改了：已经发出去的那些改成新的说法，不再发一遍。

    收件箱那一行的已读未读原样不动 —— 改一个错别字不是一件新的事。账本上的
    `payload` 也跟着改，补发写回来的才是现在这句话。
    """
    keys = select(Delivery.dedup_key).where(Delivery.event_id == event_id)
    await session.execute(
        update(Notification)
        .where(Notification.delivery_key.in_(keys))
        .values(metadata_payload=payload)
    )
    await session.execute(
        update(Delivery).where(Delivery.event_id == event_id).values(payload=payload)
    )
    await session.flush()


async def settle(session: AsyncSession, event_id: uuid.UUID, outcome: dict) -> None:
    """这件事办完了：它发出去的每一行记下结果、结掉、标成已读。

    通知是一条事件记录，等的那件事办完之后它还躺在收件箱里，照旧说着「等你」——
    一句已经不成立的话，还要人自己去点「已读」。结果并进 `payload`，渲染的一侧
    据此说现在的状态；账本上的 `payload` 也跟着改，补发写回来的才是现在这句话。
    """
    keys = select(Delivery.dedup_key).where(Delivery.event_id == event_id)
    await _settle(session, keys, outcome, delivery_where=Delivery.event_id == event_id)


async def settle_for(
    session: AsyncSession, event_id: uuid.UUID, recipient: str, outcome: dict
) -> None:
    """`settle`, for one recipient only: the thing is done as far as he is
    concerned, while the others it reached are still waited on (a person
    skipping a question several people were asked)."""
    cond = (Delivery.event_id == event_id) & (Delivery.recipient_handle == recipient)
    keys = select(Delivery.dedup_key).where(cond)
    await _settle(session, keys, outcome, delivery_where=cond)


async def settle_card(
    session: AsyncSession, card_id: uuid.UUID, outcome: dict
) -> set[uuid.UUID]:
    """这张验收卡办完了：关于它的每一条提示一并结掉。

    一张卡等的是**一件事**，说的话却有好几句：递上来时「待你审阅」，检查全绿时
    「可以采纳了」，机器推不动时「不会自动合并」。一次决定把这几句一起作废，而它
    们是几条各投各的事件（`dedup_key` 跟事件走，一条事件一个人只发一次），所以按
    事件结不掉 —— 卡的身份随每一句一起投出去（`payload.cardId`，
    `agent/announce.py`），一个卡号就够。

    结的是「等验收人动手」的那些；说**结局**的那几条（合了、作废了、PR 被关了）
    不带卡号：它们正是在这个决定之后才说出来的。返回结掉的是哪几件事（事件 id）：
    其中芝士请人决定这张卡的那道题（`agent.ask.ask_to_decide`），事件 id 就是题的
    id，调用方据此把题收起来。
    """
    cond = Delivery.payload["cardId"].as_string() == str(card_id)
    return await _settle(
        session,
        select(Delivery.dedup_key).where(cond),
        outcome,
        delivery_where=cond,
    )


async def _settle(
    session: AsyncSession,
    keys: Select,
    outcome: dict,
    *,
    delivery_where=None,
) -> set[uuid.UUID]:
    """把 `keys` 选出来的那几笔投递结掉；返回它们是哪几件事（事件 id）。"""
    if session.autoflush and (session.new or session.dirty or session.deleted):
        await session.flush()
    now = _utcnow()
    patch = cast(outcome, JSONB)
    empty = cast({}, JSONB)
    notif_ids = set(
        (
            await session.scalars(
                update(Notification)
                .where(Notification.delivery_key.in_(keys))
                .values(
                    metadata_payload=func.coalesce(
                        Notification.metadata_payload, empty
                    ).op("||")(patch),
                    read=True,
                    resolved_at=now,
                )
                .returning(Notification.id)
                .execution_options(synchronize_session=False)
            )
        ).all()
    )
    delivery_cond = (
        delivery_where if delivery_where is not None else Delivery.dedup_key.in_(keys)
    )
    settled = (
        await session.execute(
            update(Delivery)
            .where(delivery_cond)
            .values(
                payload=func.coalesce(Delivery.payload, empty).op("||")(patch),
            )
            .returning(Delivery.id, Delivery.event_id)
            .execution_options(synchronize_session=False)
        )
    ).all()
    deliv_ids = {row[0] for row in settled}
    if notif_ids or deliv_ids:
        for obj in session.sync_session.identity_map.values():
            if (
                isinstance(obj, Notification)
                and obj.id in notif_ids
                and not inspect(obj).expired_attributes
            ):
                attributes.set_committed_value(
                    obj,
                    "metadata_payload",
                    {**(obj.metadata_payload or {}), **outcome},
                )
                attributes.set_committed_value(obj, "read", True)
                attributes.set_committed_value(obj, "resolved_at", now)
            elif (
                isinstance(obj, Delivery)
                and obj.id in deliv_ids
                and not inspect(obj).expired_attributes
            ):
                attributes.set_committed_value(
                    obj, "payload", {**(obj.payload or {}), **outcome}
                )
    return {row[1] for row in settled}


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
