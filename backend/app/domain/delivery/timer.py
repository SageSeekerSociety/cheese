"""定时投递原语（结论 17）：任何参与者可以请求「某时刻把这条投递给我」。

## 为什么是一条原语，不是一个功能

平台**不替任何人想起来该干什么**。它不知道哪件事该在周二早上被记起，也不该知道 ——
想起来要设这个闹钟的是参与者自己，平台只负责在被要求的那个时刻把那条事件递出去。
所以这里没有规则、没有策略、没有「到点了要不要提醒」的判断：一行请求，一个时刻，
到点递出去。

收件人就是请求者，所以它不需要第二条收件人规则 —— 寻址那一份判据（`addressing.py`）
答的是「这条事件点了谁的名」，而这条事件点的是设闹钟的那个人自己。

## 到点产生的是一条投递，不是一轮被平台点起的对话

这是不变量 I12 在这条路上的样子。到点之后平台做的事是**投递**：把事件送给它点到的
那个参与者。收件人是人，它落进站内信；收件人是 agent，一轮是一条投递到达 agent 的
物理形态（`identity/arrival.py`），于是那一轮跑起来 —— 但它跑起来是因为**有人点了
它的名**，不是因为平台决定现在该让它干活。差别不在现象上，在「谁决定的」上：删掉
这一行请求，平台就什么也不会做。

所以这个模块里没有、也不许有一处写得出「跑一轮」：`submit` 收的是寻址结果
（`Addressed`），点名的是账本上那一行记着的请求者。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.core.errors import ValidationError
from app.domain.delivery.addressing import Addressed, Recipient
from app.domain.delivery.ledger import DeliveryEvent, Ledger
from app.domain.delivery.models import TimedDelivery
from app.domain.identity.arrival import Arrival, how_it_arrives
from app.domain.notification.models import NotificationType

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def deliver_at(
    session: AsyncSession,
    *,
    when: datetime,
    event: str,
    recipient: str,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> TimedDelivery:
    """记下「到 `when` 把 `event` 递给 `recipient`」，返回那一行。

    写的是调用方的 session，和请求它的那次调用一起提交：请求回滚了，闹钟跟着回滚，
    不会留下一个没有人设过的闹钟。
    """
    if when.tzinfo is None:
        # 带时区是硬要求：一个裸时刻在两台机器上是两个时刻，而这一行的全部内容就
        # 是一个时刻。
        raise ValidationError("投递时刻要带时区")
    if not event.strip():
        raise ValidationError("要递的东西不能是空的")
    row = TimedDelivery(
        id=uuid.uuid4(),
        project_id=project_id,
        topic_id=topic_id,
        recipient_handle=recipient,
        content=event,
        due_at=when,
        requested_at=_utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def deliver_due(
    sessions: SessionFactory,
    *,
    chat,
    runner,
    limit: int = 100,
) -> dict[str, int]:
    """到点的那些，一条一条递出去。

    一行递完就落 `delivered_at`，所以重启、重跑、两台机器同时扫都不会把同一条递第
    二遍 —— 递不出去的那一行留着 `delivered_at` 是空的，下一拍再来。
    """
    from app.domain.agent.runtime import addressed_to_agent

    now = _utcnow()
    delivered = 0
    async with sessions() as session:
        rows = (
            await session.scalars(
                select(TimedDelivery)
                .where(TimedDelivery.delivered_at.is_(None))
                .where(TimedDelivery.due_at <= now)
                .order_by(TimedDelivery.due_at)
                .limit(limit)
            )
        ).all()
        for row in rows:
            if how_it_arrives(row.recipient_handle) is Arrival.turn:
                runner.submit(
                    chat,
                    row.topic_id,
                    author=row.recipient_handle,
                    content=row.content,
                    addressed=addressed_to_agent(row.recipient_handle),
                )
            else:
                await Ledger(session).deliver(
                    DeliveryEvent(
                        id=row.id,
                        type=NotificationType.ROOM_NOTICE,
                        payload={
                            "topic_id": str(row.topic_id),
                            "content": row.content,
                        },
                        occurred_at=row.due_at,
                    ),
                    Addressed(
                        recipients=(
                            Recipient(
                                handle=row.recipient_handle,
                                reason="你请平台在这个时刻把它递给你",
                            ),
                        )
                    ),
                )
            row.delivered_at = now
            delivered += 1
        await session.commit()
    if delivered:
        logger.info("定时投递递出 %d 条", delivered)
    return {"delivered": delivered}
