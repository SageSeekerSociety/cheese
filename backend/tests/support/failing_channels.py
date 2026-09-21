"""两种「这一批没算送到」的渠道组合，外加假时钟。

**这里造的不是崩溃。** 账本那一行、收件箱那一行、`sent_at` 的回写在同一个事务里，
进程在提交之前没了就三样一起回滚，没有半成品要补。真正会留下「行在、`sent_at` 是
NULL」的只有一种：事务照常提交，而 `dispatch()` 返回 False。

**出事的是渠道，不是账本。** `NotificationEventHandler.dispatch()` 从不往外抛：它把
每个渠道的异常接在各自的 savepoint 里，然后返回「有没有全收下」。所以替身造的是一个
收不下的渠道，让账本照产品的样子看见 False —— 记一次尝试、不回写 `sent_at`。替身自
己抛异常就绕开了这条路：`send()` 当场接住，`_count_attempt` 一次都不走，试满
`MAX_ATTEMPTS` 变死信那一段于是从没被真形状验过。

两档形状不同，救法也不同，所以分开造：

- `channels_refuse`：一个渠道都没收下（站内信那一次写入在自己的 savepoint 里失败）。
  收件箱里什么都没有，账本上那一行是这件事仅剩的记录，靠补发救。
- `mailbox_took_it_but_not_counted`：站内信的 savepoint 提交了，排在它后面的渠道抛了
  出来，于是整批没算送到。账本上它和上一档长得一模一样，所以补发照样会再发一遍 ——
  这一档能不变成第二条通知，全靠 `notification.delivery_key` 的唯一约束。

时钟是注进去的，因为「补发时名册按事件发生的时刻取」这句话要分得清两个时刻。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notification.handlers import (
    InAppNotificationHandler,
    NotificationDelivery,
    NotificationEventHandler,
)


class ChannelRefused(Exception):
    """这个渠道收不下。`dispatch()` 接住它，把整批判成没送到。"""


class RefusingChannel:
    """怎么都收不下的渠道。"""

    name = "refusing"

    async def send_batch(self, deliveries: Sequence[NotificationDelivery]) -> None:
        raise ChannelRefused("这个渠道收不下")


def channels_refuse(session: AsyncSession) -> NotificationEventHandler:
    """一个渠道都没收下 —— 收件箱里什么都没有。"""
    return NotificationEventHandler(
        session=session, channel_handlers=[RefusingChannel()]
    )


def mailbox_took_it_but_not_counted(session: AsyncSession) -> NotificationEventHandler:
    """站内信收下了，排在它后面的渠道抛了 —— 整批没算送到。"""
    return NotificationEventHandler(
        session=session,
        channel_handlers=[
            InAppNotificationHandler(session=session),
            RefusingChannel(),
        ],
    )


class FakeClock:
    """一只手动走的表。"""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, *, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
