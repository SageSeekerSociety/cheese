"""用量那一屏里「谁在用、什么时候用、缓存命中多少」的几个读。

和 `usage.repositories` 里那几个平台读一样，每个读都带 `[since, until)` 窗口和行数
上界，在 SQL 里聚合，不把窗口里的行搬回内存。

**缓存命中率的口径。** 命中率 = 缓存读 token / 输入 token，两边都取自账本行：
`input_tokens` 在每条通路上都含缓存读和缓存写（订阅路由计量代理三项相加，网关路由
LiteLLM 的 `prompt_tokens` 本来就含），所以同一个比值在两条通路上是一个意思。

难处在于不是每一行都记了缓存：网关按天结算的那条路在修复之前一律记 0，那些行的 0
是「没记」，不是「没命中」；把它们算进分母，命中率会被压低，而且看不出来。行上没有
「记没记」这一列，所以这里按行能证明的东西来分：**一类行（同一条通路、同一种
`kind`）从窗口里它第一次记下缓存读的那一刻起算**，之前的那几行不进命中率。一类行在
窗口里一次缓存读都没有，就没有命中率（`None`），不写 0 —— 它可能是没命中，也可能是
没记，账本分不出来。被剔掉的输入 token 数单独给出（`uncounted_input_tokens`），页面
据此说明命中率覆盖了多少。

**「谁在用」的口径。** 项目里的花费按项目记，行上不记人（#394）；这里能说的只是
**谁触发了这一轮**：行的 `turn_id` 就是那条人发的消息所带的 id，消息的署名就是触发
的人。项目外的花费（个人芝士、文档问答）行上本来就记了 `user_id`。平台自己发起的轮次
（重发、调度、记忆整理）没有触发的人，归入 `unattributed`，不摊给任何人。这是触发了
多少消耗，不是贡献。
"""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import AuthorType, Block
from app.domain.project.models import Project
from app.domain.usage.models import COMPUTE_ROUTE, ResourceUsage, call_count
from app.domain.user.models import User, UserProfile

#: 「每小时」那条线和「什么时候用的」用北京时间读：用的人在这个时区。
BEIJING = timezone(timedelta(hours=8))

#: 按人拆画几行。和 `TOP_PROJECTS` 同一个上界理由。
TOP_USERS = 10

#: 最近的调用列几行。
RECENT_CALLS = 50

#: 窗口不长于这么多天时给每小时的序列；更长的窗口按天看就够了，按小时是几千个点。
HOURLY_MAX_DAYS = 7

_MODEL_CALLS = ResourceUsage.route != COMPUTE_ROUTE


def _in_window(since: datetime, until: datetime) -> tuple[Any, ...]:
    return (
        ResourceUsage.created_at >= since,
        ResourceUsage.created_at < until,
        _MODEL_CALLS,
    )


def _ratio(cache_read: int, counted_input: int) -> float | None:
    return cache_read / counted_input if counted_input > 0 else None


class UsageDetailRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _cache_columns(self, since: datetime, until: datetime) -> tuple[Any, ...]:
        """(计入命中率的输入, 其中的缓存读, 没计入的输入) 三个聚合列，以及要外连的
        那张「每类行从何时起记缓存」的表。口径见模块 docstring。"""
        first = (
            select(
                ResourceUsage.route,
                ResourceUsage.kind,
                func.min(ResourceUsage.created_at).label("first_cached"),
            )
            .where(*_in_window(since, until), ResourceUsage.cache_read_tokens > 0)
            .group_by(ResourceUsage.route, ResourceUsage.kind)
            .subquery()
        )
        counted = ResourceUsage.created_at >= first.c.first_cached
        columns = (
            func.coalesce(
                func.sum(case((counted, ResourceUsage.input_tokens), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((counted, ResourceUsage.cache_read_tokens), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(case((counted, 0), else_=ResourceUsage.input_tokens)), 0
            ),
        )
        join = and_(
            first.c.route == ResourceUsage.route, first.c.kind == ResourceUsage.kind
        )
        return columns, first, join

    async def cache_totals(self, *, since: datetime, until: datetime) -> dict:
        columns, first, join = self._cache_columns(since, until)
        row = (
            await self._session.execute(
                select(*columns)
                .select_from(ResourceUsage)
                .outerjoin(first, join)
                .where(*_in_window(since, until))
            )
        ).one()
        counted, cache_read, uncounted = int(row[0]), int(row[1]), int(row[2])
        return {
            "hit_ratio": _ratio(cache_read, counted),
            "input_tokens": counted,
            "cache_read_tokens": cache_read,
            "uncounted_input_tokens": uncounted,
        }

    async def cache_hit_by(
        self, column: Any, keys: list[Any], *, since: datetime, until: datetime
    ) -> dict[Any, float | None]:
        """``keys`` 里每一个（模型名、项目 id）的命中率；没有可计的输入时是 None。"""
        if not keys:
            return {}
        columns, first, join = self._cache_columns(since, until)
        rows = (
            await self._session.execute(
                select(column, *columns[:2])
                .select_from(ResourceUsage)
                .outerjoin(first, join)
                .where(*_in_window(since, until), column.in_(keys))
                .group_by(column)
            )
        ).all()
        return {row[0]: _ratio(int(row[2]), int(row[1])) for row in rows}

    async def models_of_projects(
        self, project_ids: list[Any], *, since: datetime, until: datetime
    ) -> dict[Any, list[dict]]:
        """每个项目用了哪些模型，各多少：项目一行里的模型拆分，token 多的在前。"""
        if not project_ids:
            return {}
        rows = (
            await self._session.execute(
                select(
                    ResourceUsage.project_id,
                    ResourceUsage.model,
                    func.coalesce(func.sum(ResourceUsage.total_tokens), 0),
                    func.coalesce(func.sum(ResourceUsage.cost_usd), 0.0),
                )
                .where(
                    *_in_window(since, until),
                    ResourceUsage.project_id.in_(project_ids),
                )
                .group_by(ResourceUsage.project_id, ResourceUsage.model)
                .order_by(func.sum(ResourceUsage.total_tokens).desc())
            )
        ).all()
        out: dict[Any, list[dict]] = {}
        for project_id, model, tokens, cost in rows:
            out.setdefault(project_id, []).append(
                {"model": model or "", "tokens": int(tokens), "cost_usd": float(cost)}
            )
        return out

    def _triggered_by(self, since: datetime, until: datetime) -> tuple[Any, Any]:
        """每一行是谁触发的（handle），以及要外连的两张表。见模块 docstring。"""
        turns = select(ResourceUsage.turn_id).where(
            *_in_window(since, until),
            ResourceUsage.user_id.is_(None),
            ResourceUsage.turn_id.is_not(None),
        )
        trigger = (
            select(
                Block.turn_id.label("turn_id"),
                func.min(Block.author).label("author"),
            )
            .where(
                Block.author_type == AuthorType.participant,
                Block.turn_id.in_(turns),
            )
            .group_by(Block.turn_id)
            .subquery()
        )
        return func.coalesce(User.username, trigger.c.author), trigger

    async def top_users(
        self, *, since: datetime, until: datetime, limit: int
    ) -> tuple[list[dict], dict]:
        """触发消耗最多的几个人，以及没有触发者的那部分合计。"""
        handle, trigger = self._triggered_by(since, until)
        columns, first, join = self._cache_columns(since, until)
        tokens = func.coalesce(func.sum(ResourceUsage.total_tokens), 0)
        stmt = (
            select(
                handle.label("handle"),
                tokens,
                call_count(),
                func.coalesce(func.sum(ResourceUsage.cost_usd), 0.0),
                *columns[:2],
            )
            .select_from(ResourceUsage)
            .outerjoin(
                trigger,
                and_(
                    ResourceUsage.user_id.is_(None),
                    trigger.c.turn_id == ResourceUsage.turn_id,
                ),
            )
            .outerjoin(User, User.id == ResourceUsage.user_id)
            .outerjoin(first, join)
            .where(*_in_window(since, until))
            .group_by(handle)
        )
        ranked = stmt.order_by(handle.is_(None).desc(), tokens.desc()).limit(limit + 1)
        rows = (await self._session.execute(ranked)).all()
        unattributed = {"tokens": 0, "calls": 0, "cost_usd": 0.0}
        people: list[dict] = []
        for who, tok, calls, cost, counted, cache_read in rows:
            if who is None:
                unattributed = {
                    "tokens": int(tok),
                    "calls": int(calls),
                    "cost_usd": float(cost),
                }
                continue
            people.append(
                {
                    "handle": who,
                    "tokens": int(tok),
                    "calls": int(calls),
                    "cost_usd": float(cost),
                    "cache_hit": _ratio(int(cache_read), int(counted)),
                }
            )
        people = people[:limit]
        names = await self._names([p["handle"] for p in people])
        for person in people:
            person["name"] = names.get(person["handle"].lower())
        return people, unattributed

    async def _names(self, handles: list[str]) -> dict[str, str]:
        """handle（小写）→ 昵称。账号已删或没有资料的 handle 不在里面。"""
        if not handles:
            return {}
        rows = (
            await self._session.execute(
                select(User.username, UserProfile.nickname)
                .join(UserProfile, UserProfile.user_id == User.id)
                .where(
                    func.lower(User.username).in_([h.lower() for h in handles]),
                    User.deleted_at.is_(None),
                    UserProfile.deleted_at.is_(None),
                )
            )
        ).all()
        return {username.lower(): nickname for username, nickname in rows}

    async def hourly(self, *, since: datetime, until: datetime) -> list[dict]:
        """窗口里每个整点的 token / 调用 / 金额，北京时间，补齐到当前这个小时。

        补 0 的理由和按天的序列一样（缺的小时不补，折线会把它连过去），但只补到
        现在：窗口的右端是今天 UTC 的午夜，此刻之后的小时还没有发生，补成 0 就是
        把「还没到」画成「没人用」。"""
        hour = func.date_trunc("hour", ResourceUsage.created_at, "UTC")
        rows = (
            await self._session.execute(
                select(
                    hour,
                    func.coalesce(func.sum(ResourceUsage.total_tokens), 0),
                    call_count(),
                    func.coalesce(func.sum(ResourceUsage.cost_usd), 0.0),
                )
                .where(*_in_window(since, until))
                .group_by(hour)
            )
        ).all()
        by_hour = {
            at.astimezone(UTC): (int(tok), int(calls), float(cost))
            for at, tok, calls, cost in rows
        }
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        end = min(now + timedelta(hours=1), until)
        out = []
        at = since
        while at < end:
            tok, calls, cost = by_hour.get(at, (0, 0, 0.0))
            out.append(
                {
                    "hour": at.astimezone(BEIJING).isoformat(),
                    "tokens": tok,
                    "calls": calls,
                    "cost_usd": cost,
                }
            )
            at += timedelta(hours=1)
        return out

    async def recent(
        self, *, since: datetime, until: datetime, limit: int
    ) -> list[dict]:
        """窗口里最近的几行账本：什么时候、哪个项目、谁触发、哪个模型、多少钱。

        `cache_read_tokens` 在这一行不计入命中率时是 None（口径见模块 docstring）：
        那一行的 0 可能只是没记。

        「谁触发」这一格和 `top_users` 同口径地补上**显示名**（昵称，没有就用
        handle）—— 一列纯 handle 在滚动时几乎不可区分，而这一列和排行那一列说的是
        同一种人，两处不该一处有名一处没有。脸和「是不是队友」不在这里查（那是
        user 域和 identity 域各自的事），由服务层一并批一次，和 `top_users` 一样。"""
        handle, trigger = self._triggered_by(since, until)
        _, first, join = self._cache_columns(since, until)
        counted = ResourceUsage.created_at >= first.c.first_cached
        rows = (
            await self._session.execute(
                select(
                    ResourceUsage.created_at,
                    ResourceUsage.project_id,
                    Project.name,
                    handle,
                    ResourceUsage.model,
                    ResourceUsage.route,
                    ResourceUsage.kind,
                    ResourceUsage.input_tokens,
                    ResourceUsage.output_tokens,
                    case((counted, ResourceUsage.cache_read_tokens), else_=None),
                    ResourceUsage.cost_usd,
                )
                .select_from(ResourceUsage)
                .outerjoin(Project, Project.id == ResourceUsage.project_id)
                .outerjoin(
                    trigger,
                    and_(
                        ResourceUsage.user_id.is_(None),
                        trigger.c.turn_id == ResourceUsage.turn_id,
                    ),
                )
                .outerjoin(User, User.id == ResourceUsage.user_id)
                .outerjoin(first, join)
                .where(*_in_window(since, until))
                .order_by(ResourceUsage.created_at.desc())
                .limit(limit)
            )
        ).all()
        out = [
            {
                "at": at.astimezone(BEIJING).isoformat(),
                "project_id": str(project_id) if project_id else None,
                "project": project,
                "handle": who,
                "model": model or "",
                "route": route or "",
                "kind": kind,
                "input_tokens": int(input_tokens),
                "output_tokens": int(output_tokens),
                "cache_read_tokens": None if cache_read is None else int(cache_read),
                "cost_usd": float(cost),
            }
            for (
                at,
                project_id,
                project,
                who,
                model,
                route,
                kind,
                input_tokens,
                output_tokens,
                cache_read,
                cost,
            ) in rows
        ]
        names = await self._names([row["handle"] for row in out if row["handle"]])
        for row in out:
            row["name"] = names.get(row["handle"].lower()) if row["handle"] else None
        return out
