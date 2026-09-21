"""一份在「渠道没全收下」与「站内信收下了却没算送到」两档上出事的账本，外加假时钟。

**这里造的不是崩溃。** 账本那一行、收件箱那一行、`sent_at` 的回写在同一个事务里，
进程在提交之前没了就三样一起回滚，没有半成品要补。真正会留下「行在、`sent_at` 是
NULL」的只有一种：事务照常提交，而 `dispatch()` 返回 False。两档都由此而来，救法却
不一样，所以要分开造：

- `channels_refuse`：一个渠道都没收下（站内信那一次写入在自己的 savepoint 里失败）。
  收件箱里什么都没有，账本上那一行是这件事仅剩的记录，靠补发救。
- `not_counted_as_sent`：站内信的 savepoint 提交了，排在它后面的渠道抛了出来，于是
  整批没算送到。账本上它和上一档长得一模一样，所以补发照样会再发一遍 —— 这一档能不
  变成第二条通知，全靠 `notification.delivery_key` 的唯一约束。

时钟是注进去的，因为「补发时名册按事件发生的时刻取」这句话要分得清两个时刻。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.delivery.ledger import Ledger, Pending
from app.domain.notification.handlers import NotificationEventHandler


class SendRefused(Exception):
    """这一批没被收下。"""


class FakeClock:
    """一只手动走的表。"""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, *, seconds: float) -> None:
        from datetime import timedelta

        self.now += timedelta(seconds=seconds)


class FailingLedger(Ledger):
    """在指定的那一档上让这一批发不出去的账本。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        now: Callable[[], datetime],
        channels_refuse: bool = False,
        not_counted_as_sent: bool = False,
    ) -> None:
        super().__init__(session, now=now)
        self._channels_refuse = channels_refuse
        self._not_counted_as_sent = not_counted_as_sent

    async def _send_one(self, row: Pending, channels: NotificationEventHandler) -> bool:
        if self._channels_refuse:
            raise SendRefused("一个渠道都没收下，收件箱里什么都没有")
        return await super()._send_one(row, channels)

    async def _dispatch(self, row: Pending, channels: NotificationEventHandler) -> bool:
        accepted = await super()._dispatch(row, channels)
        if self._not_counted_as_sent:
            raise SendRefused("站内信收下了，这一批却没算送到")
        return accepted
