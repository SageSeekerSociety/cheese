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

import uuid
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.usage import repositories as repo


class UsageService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = repo.UsageRepository(session)

    async def project_credits(self, project_id: uuid.UUID) -> dict:
        """一个项目在算力账上的额度：总额 / 已用 / 剩余 / 是不是不限额。

        `compute_grants` 是本领域的一张表，而「这个项目还能花多少」只有它答得出 ——
        网关那把虚拟 key 上的 `max_budget` 只是这个数的换算结果。管理页要同时看「账
        上是多少」和「刹车上被设成了多少」，所以这扇门得把它转出去；别处直接摸
        `ComputeGrantRepository` 就是又开一道口径，两个数哪天漂开时没人说得清哪个对。
        """
        return await repo.ComputeGrantRepository(self._session).summary(project_id)

    async def project_credits_batch(self, projects: list) -> dict[uuid.UUID, dict]:
        """`project_credits` 的批量版：一批项目 → 各自的额度汇总。

        编排两笔批量查询（小队归属一条、grant 一条）再在 Python 里按项目归组，
        与逐项目的 `summary()` **同一口径**：eligible = 项目 earmark ∪ 本队池
        （team_id 匹配且 project_id 为 NULL）；一个 grant 都没有 = unlimited。
        排序不进汇总（`summary` 也不排序），消费顺序是 `consume` 的事。

        逐字段相等性由 `tests/unit/test_credits_batch.py` 钉着：管理页的项目
        额度表换走这条路之后，数字必须和旧路一个一个对得上。
        """
        from app.domain.project.services import ProjectService

        grants_repo = repo.ComputeGrantRepository(self._session)
        teams = await ProjectService(self._session).teams_for_projects(projects)
        team_ids = sorted({t for t in teams.values() if t is not None})
        grants = await grants_repo.list_for_scope([p.id for p in projects], team_ids)
        by_project: dict[uuid.UUID, list] = {p.id: [] for p in projects}
        for grant in grants:
            if grant.project_id is not None:
                if grant.project_id in by_project:
                    by_project[grant.project_id].append(grant)
                continue
            # 全队池：归属小队匹配的项目都 eligible。
            for project in projects:
                if (
                    grant.project_id is None
                    and grant.team_id is not None
                    and teams.get(project.id) == grant.team_id
                ):
                    by_project[project.id].append(grant)
        out: dict[uuid.UUID, dict] = {}
        for project in projects:
            eligible = by_project[project.id]
            total = sum(g.credits_total for g in eligible)
            used = sum(g.credits_used for g in eligible)
            out[project.id] = {
                "unlimited": not eligible,
                "credits_total": total,
                "credits_used": used,
                "credits_remaining": total - used,
            }
        return out

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
