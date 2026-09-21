"""一份可以在「写入之后」与「发出之后」两个点崩掉的投递账本，外加一个假时钟。

投递不是纯函数：它有状态，而且必须能被从中间打断。能不能从中间打断决定了「恰好一
次」是不是真的 —— 这两个点上崩一次，一条通知要么丢掉，要么发两遍，而两种都不会报
错，所以只能靠这里把它们造出来。

两个点各是一半真实的失败：

- `after_record`：账本那一行已经和事件一起提交，发送还没开始就没了。进程被 OOM
  杀掉、容器被换掉、渠道抛异常，都落在这一档。
- `after_dispatch`：通知已经写进收件箱，`sent_at` 还没回写就没了。账本里那一行看起
  来和「根本没发」一模一样，所以这一档只能靠去重键认出来。

时钟是注进去的，因为「补发时名册按事件发生的时刻取」这句话要分得清两个时刻。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.delivery.ledger import Ledger, Pending
from app.domain.notification.handlers import NotificationEventHandler


class Crash(Exception):
    """这个进程到此为止了。"""


class FakeClock:
    """一只手动走的表。"""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, *, seconds: float) -> None:
        from datetime import timedelta

        self.now += timedelta(seconds=seconds)


class CrashingLedger(Ledger):
    """在指定的那一点抛 `Crash` 的账本。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        now: Callable[[], datetime],
        after_record: bool = False,
        after_dispatch: bool = False,
    ) -> None:
        super().__init__(session, now=now)
        self._after_record = after_record
        self._after_dispatch = after_dispatch

    async def _send_one(self, row: Pending, channels: NotificationEventHandler) -> bool:
        if self._after_record:
            raise Crash("记下来了，还没发就崩了")
        return await super()._send_one(row, channels)

    async def _dispatch(self, row: Pending, channels: NotificationEventHandler) -> bool:
        accepted = await super()._dispatch(row, channels)
        if self._after_dispatch:
            raise Crash("发出去了，确认还没回写就崩了")
        return accepted
