"""轮次账和工具调用账上的三个数 —— 只数行，不碰实例状态。

平台看板问的：「额度拒答了多少轮」「有结果的前后都没送的轮次有多少」「工具调用
发出去了、结果永远不会回来的有多少」。三样都只读本包的两张表（`AgentTurn`、
`DispatchRow`），一行 SQL 一个数。

写在这里是为了让 `platform_stats` 不必 import 本包的模型：那个包在第 7 层、够得着
本包，可它和本包落在同一个强连通分量里（C3），模型一进来就冻一条边。于是它把要问
的写进自己的 `ports.py`，由 api 在建看板服务时把 `TurnCounts(db)` 递进去 —— 和
`webhook` 递 `announce` 是同一条规矩。类名和那个声明同名，是好让读的人一眼对上是
哪一份。
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.dispatch_log import DispatchRow
from app.domain.agent.models import AgentTurn


class TurnCounts:
    """拿一个 session 答上面那三个数；谁建谁给 session（看板服务那一侧）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _count(self, *where) -> int:
        stmt = select(func.count()).select_from(AgentTurn).where(*where)
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def credits_refused(self, *, since: datetime, until: datetime) -> int:
        """`credits_refused_at` 是**唯一**可靠的额度拒答来源：那条 notice 故意不带
        自己的 code（和通用 turn_failed 同路），按文案匹配是在拿句子当结构。"""
        return await self._count(
            AgentTurn.credits_refused_at.is_not(None),
            AgentTurn.started_at >= since,
            AgentTurn.started_at < until,
        )

    async def prompt_undelivered(self, *, since: datetime, until: datetime) -> int:
        """停了、但结果没送到收件人的轮次。"""
        return await self._count(
            AgentTurn.delivered_at.is_(None),
            AgentTurn.stopped_at.is_not(None),
            AgentTurn.started_at >= since,
            AgentTurn.started_at < until,
        )

    async def unsettled_dispatches(self) -> int:
        """工具调用发出去了、结果永远不会回来的条数。`outcome IS NULL` 即是。

        这不是「慢」，按设计就是「不会再有人写回来」（`dispatch_log` 的 docstring）。
        """
        stmt = (
            select(func.count())
            .select_from(DispatchRow)
            .where(DispatchRow.outcome.is_(None))
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)
