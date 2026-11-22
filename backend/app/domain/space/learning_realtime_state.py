"""今天学情分析的实时读取到哪了：还在读，还是到上限了、等明天。

两页（「我的学习洞察」和「数据 › 学情分析」的总览）各要一格 ``realtime``，说的都是
同一件事，所以在这里算一次：

- ``live``：还能读今天的新发言 —— 成员说完一两分钟，判断就出现；
- ``capped``：今天读取这一路的次数用完了（``read_ceiling``），页面写明「今天实时分析
  到上限，明早统一更新」。之后的发言不丢：明天那一轮从同一个游标接着读。

判据是今天的计数行与 ``read_ceiling`` 比，不是 ``run.capped`` 那一格：那一格每小时的
汇总也会写，而「读取停下来」才是这两页说那句话的时刻。块自己的上限
（``Space.learning_analysis_daily_calls``）也算在里面。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.learning_insight_analysis import (
    daily_ceiling,
    local_day,
    read_ceiling,
)
from app.domain.space.learning_insight_repositories import SpaceLearningRunRepository
from app.domain.space.models import Space


async def state(
    session: AsyncSession,
    *,
    space_id: int,
    space: Space | None = None,
    now: datetime | None = None,
) -> dict:
    """This board's realtime state right now, for a page to show."""
    if space is None:
        space = await session.get(Space, space_id)
    day, _ = local_day(now or datetime.now(UTC))
    run = await SpaceLearningRunRepository(session).of_day(space_id=space_id, day=day)
    capped = run.calls >= read_ceiling(space)
    return {
        "state": "capped" if capped else "live",
        "callsToday": run.calls,
        "dailyCalls": daily_ceiling(space),
    }
