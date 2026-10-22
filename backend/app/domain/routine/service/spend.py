"""一条规则、一次执行花了多少额度。

答案不在这个域里：账本（``resource_usage``）按**轮次**记账，一次执行是频道里的一轮，
所以「这条规则本月花了多少」是对它的许多个轮次 id 求和。这里只做两件事 —— 把一条
规则（或一页执行）的轮次 id 取出来，再把它们交给账本那一域求和
（``UsageRepository.credits_by_work``）。规则表上不加一列累计：加了就有第二个数要在
每次结算时记得更新，而轮次 id 本来就在执行行上（``RoutineRun.turn_id``）。

**为什么窗口从文件里传进来。** 「本月」是计费的口径（平台的月，北京时间每月 1 日重置，
``usage/windows.month_start``），不是这一域自己的钟。这里只负责照着窗口筛，不自己
算一个「月」出来 —— 两处各算一个，月末就会对不上。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.routine.models import RoutineRun
from app.domain.routine.service._shared import REPORT_TIMEOUT
from app.domain.usage.services import UsageService


async def credits_by_rule(
    session: AsyncSession,
    routine_ids: list[uuid.UUID],
    *,
    since: datetime,
    until: datetime,
) -> dict[uuid.UUID, float]:
    """每条规则在 ``[since, until)`` 里扣掉的额度（点）。一次查完这一批。

    没花过的规则是 0，也在结果里 —— 列表上每一行都要画一个数，「没有这一条」和
    「花了 0」对画的人是一回事。

    执行行按 ``created_at >= since - REPORT_TIMEOUT`` 取：花费是这一轮跑完才落账的
    （可能几十分钟后），月初那一刻开始的那一次，钱落在本月、执行行却是上月建的。
    ``REPORT_TIMEOUT`` 正好是「一次执行最长能跑多久再加结算的余量」，用它当下界既够
    也说得清；真正的月份过滤由账本那一边的 ``created_at`` 做，这里只收窄候选集。
    """
    if not routine_ids:
        return {}
    rows = await session.execute(
        select(RoutineRun.routine_id, RoutineRun.turn_id).where(
            RoutineRun.routine_id.in_(routine_ids),
            RoutineRun.turn_id.is_not(None),
            RoutineRun.created_at >= since - REPORT_TIMEOUT,
        )
    )
    work_of: dict[uuid.UUID, list[uuid.UUID]] = {}
    for routine_id, turn_id in rows.all():
        work_of.setdefault(routine_id, []).append(turn_id)
    by_work = await UsageService(session).credits_by_work(
        [turn_id for turn_ids in work_of.values() for turn_id in turn_ids],
        since=since,
        until=until,
    )
    return {
        routine_id: sum(by_work.get(turn_id, 0.0) for turn_id in turn_ids)
        for routine_id, turn_ids in work_of.items()
    }


async def credits_by_run(
    session: AsyncSession,
    runs: list[RoutineRun],
    *,
    since: datetime,
    until: datetime,
) -> dict[uuid.UUID, float]:
    """这一批执行各自在窗口里扣掉的额度（点），按 ``RoutineRun.id`` 键。

    还没跑完的那几次（``turn_id`` 是空）不出现在结果里：它没有花费可言，画的人读不到
    就当「没有数」，而不是当 0 —— 刚跑完的那一次花费要几秒到几十秒才落账，那一段里
    写 0 是在说「这一轮没花钱」，写「结算中」才是真的。
    """
    work_ids = [r.turn_id for r in runs if r.turn_id is not None]
    by_work = await UsageService(session).credits_by_work(
        work_ids, since=since, until=until
    )
    return {
        r.id: by_work[r.turn_id]
        for r in runs
        if r.turn_id is not None and r.turn_id in by_work
    }
