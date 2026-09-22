"""用量的读 model：别的领域要看用量，从这道门进。

`resource_usage` 每调一次 `/v1/messages` 长一行，所以这几个读有两件约定俗成的事
——**窗口必填**（两端由 `utc_day_window` 算好传进来，没有 WHERE 的全表聚合在页面上
一次也不允许出现）、**分桶下推成 SQL 的 `GROUP BY`**（否则一整天几十万行要搬回内存
再在 Python 里按天累加）。它们和查询一起写在 `UsageRepository` 那一层，这里只把
看板要的三个读转出去，不加一处算术：调用点自己拼 select 就等于把上面两条各自重答
一遍，而两处答出两个口径时，两边看着都对。

叫 `UsageService` 是跟领域走（`UsageRepository` → `UsageService`），形状和
`FeedbackService` / `AdminService` 一样：本域对外只有这一扇门，别的领域不必知道仓储
叫什么、有几个。
"""

from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.usage import repositories as repo


class UsageService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = repo.UsageRepository(session)

    async def platform_totals(self, *, since: datetime, until: datetime) -> dict:
        """窗口内的总量：tokens / calls / cost_usd / unpriced_tokens。

        `unpriced_tokens` 必须和 `cost_usd` 一起给：订阅按月计费，行上的
        `cost_usd = 0.0` 意思是**没有价**而不是免费，少了它，几百万 token 上印一个
        `$0.0000` 读起来像「这个月没花钱」。
        """
        return await self._repo.platform_totals(since=since, until=until)

    async def platform_series(
        self, *, since: datetime, until: datetime
    ) -> dict[date, dict]:
        """按 UTC 的天分好的用量序列，稀疏；补 0 是调用方的事。"""
        return await self._repo.platform_series(since=since, until=until)

    async def by_model(
        self, *, since: datetime, until: datetime, limit: int
    ) -> list[dict]:
        """窗口内按模型拆的用量。`limit` 和 `top_projects` 同一个理由。"""
        return await self._repo.by_model(since=since, until=until, limit=limit)

    async def by_route(self, *, since: datetime, until: datetime) -> list[dict]:
        """窗口内按供给通路拆的用量（网关 / 订阅 / 自带凭据）。"""
        return await self._repo.by_route(since=since, until=until)

    async def top_projects(
        self, *, since: datetime, until: datetime, limit: int
    ) -> list[dict]:
        """窗口内用量最高的几个项目（id + 名字 + tokens + cost_usd）。

        `limit` 是查询的一半而不是省事的默认值：不带它的 top-N 在一个每天增长的表
        上等于把整个窗口的行排一遍序。
        """
        return await self._repo.top_projects(since=since, until=until, limit=limit)
