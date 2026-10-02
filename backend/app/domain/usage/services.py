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

from app.core.config import settings
from app.domain.usage import ledger
from app.domain.usage import repositories as repo


def _gateway_budget(balance: ledger.Balance, spent_usd: float) -> float | None:
    """The ``max_budget`` a project's gateway key should carry: what the key
    has spent so far, plus what the project may still spend, in USD. None is no
    brake at all: an unlimited plan, or no price per credit to convert with."""
    price = settings.llm_gateway_credit_usd
    if balance.unlimited or not price:
        return None
    return round(spent_usd + max(0.0, balance.credits_remaining) * price, 4)


class UsageService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = repo.UsageRepository(session)

    async def project_credits(self, project_id: uuid.UUID) -> dict:
        """一个项目在算力账上的额度：总额 / 已用 / 剩余 / 是不是不限额。

        额度包只有 `usage.ledger` 答得出「这个项目还能花多少」—— 网关那把虚拟 key
        上的 `max_budget` 只是这个数的换算结果。管理页要同时看「账上是多少」和
        「刹车上被设成了多少」，所以这扇门得把它转出去。
        """
        payer = await ledger.payer_for_project(self._session, project_id)
        return (await self._summaries({project_id: payer}))[project_id]

    async def plan_model_tiers(self, team_id: int) -> frozenset[str] | None:
        """The model tiers the team's plan allows; None is every tier."""
        return (await ledger.team_terms(self._session, team_id)).model_tiers

    async def admit_project(self, project_id: uuid.UUID):
        """Whether a call in this project may run now: None, or the
        ``ledger.Refusal`` saying why not and until when."""
        payer = await ledger.payer_for_project(self._session, project_id)
        return await ledger.Ledger(self._session).admit(payer)

    async def project_credits_batch(self, projects: list) -> dict[uuid.UUID, dict]:
        """`project_credits` 的批量版：一批项目 → 各自的额度汇总，一条查询取回
        额度包，再按与逐项目同一条规则（`ledger._applies`）归给各项目。"""
        payers = await ledger.payers_for_projects(self._session, projects)
        return await self._summaries(payers)

    async def _summaries(self, payers: dict) -> dict[uuid.UUID, dict]:
        balances = await ledger.Ledger(self._session).balances(payers)
        spent = await self._repo.gateway_spend(list(payers))
        out = {}
        for key, balance in balances.items():
            summary = balance.summary()
            summary["gateway_budget_usd"] = _gateway_budget(
                balance, spent.get(key, 0.0)
            )
            out[key] = summary
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
