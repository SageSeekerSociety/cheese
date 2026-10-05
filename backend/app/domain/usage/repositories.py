"""Resource usage data access + aggregation."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Uuid, case, column, func, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.platform_stats.windows import utc_day
from app.domain.project.models import Project
from app.domain.usage.models import COMPUTE_ROUTE, ResourceUsage

#: Rows of model calls. A compute charge (``usage.compute``) is a usage row
#: too, for its credits, but it has no tokens and is no call: every count of
#: tokens, cost and calls below leaves it out, and only the credit reads of a
#: team's spend take it in.
MODEL_CALLS = ResourceUsage.route != COMPUTE_ROUTE

# Which room a task hangs in, read as a bare table: ``room_task`` depends on
# this domain, so importing its model back would make the two a cycle.
_tasks = table("tasks", column("id", Uuid), column("room_id", Uuid))


def unpriced_tokens() -> Any:
    """Tokens whose USD price is not knowable — one definition, three readers.

    A row of a model with no rate carries ``cost_usd = 0.0`` meaning "no
    price", not "free". Reported separately so the UI can say 未知 instead of
    printing $0.0000 over millions of tokens ("未知冒充零").

    Written once because the per-key aggregate (`_agg`) and the platform-wide one
    (`platform_totals`) must agree about what 「未定价」 means; two copies of this
    predicate is how a dashboard and a project page come to report different
    numbers over the same rows.
    """
    return func.sum(
        case(
            (
                (ResourceUsage.cost_usd <= 0.0) & (ResourceUsage.total_tokens > 0),
                ResourceUsage.total_tokens,
            ),
            else_=0,
        )
    )


class UsageRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID | None,
        topic_id: uuid.UUID | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cache_write_1h_tokens: int = 0,
        kind: str = "chat",
        metered: bool = True,
        route: str = "",
        turn_id: uuid.UUID | None = None,
        user_id: int | None = None,
        team_id: int | None = None,
        credits: float = 0.0,
    ) -> ResourceUsage:
        """Record spend against its originating message or platform work id.

        ``metered=False`` records work whose token counts are NOT knowable —
        the hooks backends run interactive Claude Code, which reports no usage
        locally, and the gateway that would supply it is not configured
        everywhere. Such work used to be skipped entirely, so the table showed
        an empty month while real money drained: 300 RMB of relay credit went
        without a single row naming what spent it. A row with zero tokens is
        still worth writing — it says work happened, on which project, with
        which model, which is the difference between "we do not know how much"
        and "we do not know anything".

        Spend outside any project has no ``project_id`` and names, in
        ``user_id``, the person whose credits paid for it.
        """
        # `topic_id` names the conversation the spend happened in: a room's own
        # line, or a task's, whose session is its own and so is its bill. The
        # row keeps the room, with the task beside it.
        task_id = None
        if topic_id is not None:
            room_id = await self._session.scalar(
                select(_tasks.c.room_id).where(_tasks.c.id == topic_id)
            )
            if room_id is not None:
                topic_id, task_id = room_id, topic_id
        row = ResourceUsage(
            project_id=project_id,
            user_id=user_id,
            topic_id=topic_id,
            task_id=task_id,
            turn_id=turn_id,
            model=model,
            input_tokens=input_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_write_1h_tokens=cache_write_1h_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_usd=cost_usd,
            kind=kind if metered else f"{kind}:unmetered",
            route=route,
            team_id=team_id,
            credits=credits,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def team_spend_by_day(
        self, team_id: int, *, since: datetime, until: datetime, tz: str
    ) -> list[tuple[date, uuid.UUID | None, str, float]]:
        """Credits ``team_id`` was charged in ``[since, until)``, summed per
        local day in ``tz``, project and kind — the grouping done in SQL."""
        day = func.date(func.timezone(tz, ResourceUsage.created_at))
        rows = await self._session.execute(
            select(
                day,
                ResourceUsage.project_id,
                ResourceUsage.kind,
                func.sum(ResourceUsage.credits),
            )
            .where(
                ResourceUsage.team_id == team_id,
                ResourceUsage.created_at >= since,
                ResourceUsage.created_at < until,
            )
            .group_by(day, ResourceUsage.project_id, ResourceUsage.kind)
        )
        return [(d, p, k, float(c or 0.0)) for d, p, k, c in rows.all()]

    async def gateway_spend(self, project_ids: list[uuid.UUID]) -> dict:
        """Project → USD its gateway key has been recorded spending, ever."""
        if not project_ids:
            return {}
        rows = await self._session.execute(
            select(ResourceUsage.project_id, func.sum(ResourceUsage.cost_usd))
            .where(
                ResourceUsage.project_id.in_(project_ids),
                ResourceUsage.route == "gateway",
            )
            .group_by(ResourceUsage.project_id)
        )
        return {pid: float(spent or 0.0) for pid, spent in rows}

    async def _agg(self, column, value) -> dict:
        # The public `turns` key is retained for wire compatibility, but its
        # number now means distinct originating human messages or platform work
        # ids, not intervals. One attributed unit can write several rows: the
        # metering proxy logs every /v1/messages call and the gateway may land a
        # deferred backfill. Rows without attribution still count once each.
        unattributed = func.sum(case((ResourceUsage.turn_id.is_(None), 1), else_=0))
        attributed_work = func.count(func.distinct(ResourceUsage.turn_id))
        # Tokens whose USD price is not knowable — see `unpriced_tokens()`.
        unpriced = unpriced_tokens()
        stmt = select(
            func.coalesce(func.sum(ResourceUsage.input_tokens), 0),
            func.coalesce(func.sum(ResourceUsage.output_tokens), 0),
            func.coalesce(func.sum(ResourceUsage.total_tokens), 0),
            func.coalesce(func.sum(ResourceUsage.cost_usd), 0.0),
            attributed_work,
            func.coalesce(unattributed, 0),
            func.coalesce(unpriced, 0),
        ).where(column == value, MODEL_CALLS)
        row = (await self._session.execute(stmt)).one()
        return {
            "input_tokens": int(row[0]),
            "output_tokens": int(row[1]),
            "total_tokens": int(row[2]),
            "cost_usd": float(row[3]),
            "turns": int(row[4]) + int(row[5]),
            "unpriced_tokens": int(row[6]),
        }

    async def for_topic(self, topic_id: uuid.UUID) -> dict:
        """A room's TOTAL — its own main line and every thread dispatched in it.

        Threads are included by construction rather than by a union: `add`
        stores the room in `topic_id` whichever half of the place the spend
        happened in, so this one predicate already reaches all of it.
        """
        return await self._agg(ResourceUsage.topic_id, topic_id)

    async def for_task(self, task_id: uuid.UUID) -> dict:
        """One thread's own spend, and nothing of the room around it."""
        return await self._agg(ResourceUsage.task_id, task_id)

    async def last_model_by_task(
        self, task_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """每条活最后一次花钱花在哪个模型上，一次查完整个房间。

        卡上的模型是从这里算出来的，不是一列存着的状态（`presentation.card_model`）。
        取最后一行而不是取「用得最多的」：卡回答的是「现在用的是什么」，而一条改过
        绑定的活，历史上那些行说的是它改之前的事。

        空 `model` 的行跳过 —— 那种行说的是「有活动，但不知道花了多少」
        （`add(metered=False)`），它不足以说出用的是哪个模型。

        `DISTINCT ON` 让库只发回每条活一行。计量代理是每一次 `/v1/messages` 写
        一行（见本文件开头），一条跑了半天的活就是上千行；把整个房间的用量行全
        取回内存、再靠字典覆盖留下最后一条，刷一次看板就要搬一遍这些行然后扔掉。
        """
        if not task_ids:
            return {}
        rows = (
            await self._session.execute(
                select(ResourceUsage.task_id, ResourceUsage.model)
                .where(
                    ResourceUsage.task_id.in_(task_ids),
                    ResourceUsage.model != "",
                )
                .distinct(ResourceUsage.task_id)
                .order_by(ResourceUsage.task_id, ResourceUsage.created_at.desc())
            )
        ).all()
        return {task_id: model for task_id, model in rows if task_id is not None}

    async def for_project(self, project_id: uuid.UUID) -> dict:
        return await self._agg(ResourceUsage.project_id, project_id)

    async def platform_totals(self, *, since: datetime, until: datetime) -> dict:
        """全平台在窗口内的用量总量 —— 看板「用量」那一块的头三个数。

        **窗口是必填的**，不是省事的默认：`resource_usage` 是全平台增长最快的一张
        表（每调一次 `/v1/messages` 一行），一个没有 WHERE 的全表聚合在页面上一次
        也不允许出现。这里的两端由 `utc_day_window` 算好传进来，条件照旧是半开的
        `>= since AND < until`。

        `calls` 是**行数**，也就是计量代理记下的 `/v1/messages` 调用次数 —— 一行一次
        调用。它和 `_agg` 的 `turns` 不是一回事（那个数的是「有归属的单元」，一个
        单元可以写好几行），两个数回答的是不同的问题，所以各叫各的名字。

        `unpriced_tokens` 走 `unpriced_tokens()`，和单键聚合共用同一条判据。
        """
        stmt = select(
            func.coalesce(func.sum(ResourceUsage.total_tokens), 0),
            func.count(),
            func.coalesce(func.sum(ResourceUsage.cost_usd), 0.0),
            func.coalesce(unpriced_tokens(), 0),
        ).where(
            ResourceUsage.created_at >= since,
            ResourceUsage.created_at < until,
            MODEL_CALLS,
        )
        row = (await self._session.execute(stmt)).one()
        return {
            "tokens": int(row[0]),
            "calls": int(row[1]),
            "cost_usd": float(row[2]),
            "unpriced_tokens": int(row[3]),
        }

    async def platform_series(
        self, *, since: datetime, until: datetime
    ) -> dict[date, dict]:
        """按 **UTC 的天**分好的用量序列，稀疏：有数据的那天才有一行。

        补 0 不在这里做（那是 `windows.dense_series` 的事）：这一层唯一的职责是把
        分桶下推成 SQL 的 `GROUP BY`。不这么做就要把窗口里每一行搬回内存再在 Python
        里按天累加，而这是那张每调一次接口长一行的表 —— 一个整天的窗口就是几十万行。
        """
        day = utc_day(ResourceUsage.created_at)
        stmt = (
            select(
                day.label("day"),
                func.coalesce(func.sum(ResourceUsage.total_tokens), 0),
                func.count(),
                func.coalesce(func.sum(ResourceUsage.cost_usd), 0.0),
            )
            .where(
                ResourceUsage.created_at >= since,
                ResourceUsage.created_at < until,
                MODEL_CALLS,
            )
            .group_by(day)
            .order_by(day)
        )
        rows = (await self._session.execute(stmt)).all()
        return {
            row[0].date(): {
                "tokens": int(row[1]),
                "calls": int(row[2]),
                "cost_usd": float(row[3]),
            }
            for row in rows
        }

    async def by_model(
        self, *, since: datetime, until: datetime, limit: int
    ) -> list[dict]:
        """窗口内按**模型**拆的用量 —— 「钱花在哪个模型上」。

        `route` 那一栏是「这笔供给从哪条路来的」（网关 / 订阅 / 自带凭据），和模型
        是两个正交的切口：同一个模型可以走订阅也可以走网关。两个一起给，「贵的是
        模型还是计费方式」这个问题才答得出来。

        `cost_usd` 与 `unpriced_tokens` 同时出现在每一行上，理由和 `platform_totals`
        一样：没有单价的那部分 0 是「没有价」不是「免费」，少写这一列，一根柱子会在
        几百万 token 上印一个 `$0.0000`。
        """
        day_unused = None  # 窗口聚合与 by_day 同源，这里只按模型分组
        del day_unused
        stmt = (
            select(
                ResourceUsage.model,
                func.coalesce(func.sum(ResourceUsage.total_tokens), 0),
                func.count(),
                func.coalesce(func.sum(ResourceUsage.cost_usd), 0.0),
                func.coalesce(unpriced_tokens(), 0),
            )
            .where(
                ResourceUsage.created_at >= since,
                ResourceUsage.created_at < until,
                MODEL_CALLS,
            )
            .group_by(ResourceUsage.model)
            .order_by(func.sum(ResourceUsage.total_tokens).desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            {
                "model": row[0] or "",
                "tokens": int(row[1]),
                "calls": int(row[2]),
                "cost_usd": float(row[3]),
                "unpriced_tokens": int(row[4]),
            }
            for row in rows
        ]

    async def by_route(self, *, since: datetime, until: datetime) -> list[dict]:
        """窗口内按**供给通路**拆的用量 —— 网关 / 订阅 / 自带凭据。

        这是「未定价 token 到底是哪来的」的那个答案：`unpriced_tokens` 只告诉读者
        「有一部分算不出价」，按通路拆开之后能看到那部分在哪条路上。行数就是通路的
        个数（三条），所以不设 limit。
        """
        stmt = (
            select(
                ResourceUsage.route,
                func.coalesce(func.sum(ResourceUsage.total_tokens), 0),
                func.count(),
                func.coalesce(func.sum(ResourceUsage.cost_usd), 0.0),
                func.coalesce(unpriced_tokens(), 0),
            )
            .where(
                ResourceUsage.created_at >= since,
                ResourceUsage.created_at < until,
                MODEL_CALLS,
            )
            .group_by(ResourceUsage.route)
            .order_by(func.sum(ResourceUsage.total_tokens).desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            {
                "route": row[0] or "",
                "tokens": int(row[1]),
                "calls": int(row[2]),
                "cost_usd": float(row[3]),
                "unpriced_tokens": int(row[4]),
            }
            for row in rows
        ]

    async def top_projects(
        self, *, since: datetime, until: datetime, limit: int
    ) -> list[dict]:
        """窗口内用量最高的几个项目 —— 柱子图上每一根柱子。

        返回 `project_id` 和名字，而且**是 join 出来的**：柱子要能点进去，只给一个
        名字的柱子点不开；只给 id 的柱子画不出标签。名字取自 `projects.name`，不
        缓存一份到用量行上 —— 项目改名之后柱子的标题要和项目页一致。

        行数由 `limit` 收口（看板只画前几根），窗口收口时间范围：两个上界都在，不
        带 `limit` 的 top-N 在一个每天增长的表上等于把整个窗口的行排序一遍。
        """
        stmt = (
            select(
                ResourceUsage.project_id,
                Project.name,
                func.coalesce(func.sum(ResourceUsage.total_tokens), 0),
                func.coalesce(func.sum(ResourceUsage.cost_usd), 0.0),
            )
            .join(Project, Project.id == ResourceUsage.project_id)
            .where(
                ResourceUsage.created_at >= since,
                ResourceUsage.created_at < until,
                MODEL_CALLS,
            )
            .group_by(ResourceUsage.project_id, Project.name)
            .order_by(func.sum(ResourceUsage.total_tokens).desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            {
                "project_id": str(row[0]),
                "name": row[1],
                "tokens": int(row[2]),
                "cost_usd": float(row[3]),
            }
            for row in rows
        ]
