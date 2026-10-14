"""每个席位生成得有多快 —— 一个能被查的数。

**口径取的是「按轮速率」**：一轮的 `output_tokens` 除以这一轮从起到止的墙钟秒数，
再按席位 × 模型 × 供给取 p50 / p90。另一条候选是「窗口吞吐」
（`sum(output_tokens) ÷ 窗口秒数`），它也留在结果里，叫 `window_tps`，但不是主口径。

**为什么选按轮速率。** 两半都有得记：`agent_turns` 一行一轮，带 `started_at` /
`stopped_at` / `agent_handle`（`agent.models.AgentTurn`）；`resource_usage` 一行一
次计量，带 `turn_id` / `output_tokens` / `model` / `route`
（`usage.models.ResourceUsage`）。按 `turn_id` 一接，一轮的产出和它自己的时长就在同一
行上。窗口吞吐做不到这一点：它把坐着等的空档也算进分母，一个被限流、半天不动的席位
会显得「慢」，而不是「被掐了」——那正是这条读法要分开的事。

**分母是整轮的墙钟，不是生成时长。** 一轮里有思考、有工具调用、有生成，这里全算
进去。纯的「流式 TPS」（只数吐字那几秒）平台记不出来，依据是：`resource_usage` 没有
比 `created_at` 更细的时刻，而一次归属的用量在这张表上会落成好几行——计量代理每个
`/v1/messages` 调用写一行，网关一次灌出把这个模型自上次以来的许多次调用记成一行。
所以行数可以多于归属单元数（`usage.models.ResourceUsage.turn_id` 的列注释写着这一
点），调用次数则取 `sum(calls)` 不取行数（`calls` 那一列的注释）。这里对每一行做
的是求和，速率的样本仍然一轮一个。`agent_turns` 也只有起、投递、止三个时刻。
要真正的吐字速率，得让模型服务那边按调用记首尾时刻——那不是这张表能补的。所以读到
的数是「这一轮平均每秒产出多少 token」，不是「生成时每秒吐多少字」。

**`output_tokens = 0` 的行不算。** 那不是「跑过但没数」，是这一轮没拿到用量时按席位
配置补写的标记：`model` 写席位设的模型、`route` 写当轮路由，都不代表实际服务它的
模型（见团队记忆《直接查平台库时有四处会骗人》第 4 条）。放进来会造出一批假的 0 速
率和假的模型归属，2026-10-04 数出过 131 条假 opus 记录。判实跑只看
`output_tokens > 0`。

**供给分开列。** `route` 是流量实际走的那条道（`gateway` / `subscription` /
`native` / `compute`，老行是空串）。分开列，被限流的席位才看得出是「速率掉了」，
不是「没在跑」——一个只跑 `subscription` 的席位掉速时，`gateway` 那几行的缺席和它
自己那几行的降速是两件事。

**座席是 `agent_handle`，不是房间。** 一个房间并排坐着几个队友，`agent_turns` 上的
`agent_handle` 才是这一轮是谁在生成（`None` 是没人认领的一轮：会话自己起的，或者
装配前就没了）。用量表自己没有席位，只能从轮次这边接过来。

**秒数为 0 的一轮不进速率分位，但它的 token 照算。** 起止同一刻（回填、或者时钟
跳）没有速率可言，除出来的无穷大会把 p90 顶飞。它仍然是一次真实产出，所以留在这
一轮的 `output_tokens` 和 `turns` 里，只是不进 `p50_tps` / `p90_tps` 的样本。
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.models import AgentTurn
from app.domain.run_record.admin import WINDOWS
from app.domain.usage.models import ResourceUsage


class SeatRateOut(BaseModel):
    """一行 = 一个席位 × 一个模型 × 一条供给上的速率。"""

    #: `agent_turns.agent_handle`，空串表示这一轮没人认领。
    seat: str
    model: str
    #: `resource_usage.route`：`gateway` / `subscription` / `native` / `compute`，
    #: 老行是空串。
    supply: str
    #: 这一组里有几轮（有产出、而且跑完了的）。
    turns: int
    output_tokens: int
    #: 模型调用次数：`sum(calls)`，不是行数 —— 网关一栏能把多次调用灌成一行。
    calls: int
    #: 这些轮的墙钟秒数合计，分母的原料。
    turn_seconds: float
    #: 每轮速率的中位数 / 九成分位（`output_tokens ÷ 整轮秒数`）。空 = 这一组里
    #: 没有一轮的秒数大于 0，速率无从谈起。
    p50_tps: float | None
    p90_tps: float | None
    #: 窗口吞吐：`output_tokens ÷ 窗口秒数`，把坐着等的空档也算进分母。和上面两个
    #: 一起读：它们同时掉才是真的慢，只掉这个是这席位闲着。
    window_tps: float


class SeatThroughputOut(BaseModel):
    window: str
    since: str
    now: str
    #: 这个窗口一共多少秒，`window_tps` 的分母。
    window_seconds: float
    #: 按席位 × 模型 × 供给。
    rows: list[SeatRateOut]
    #: 只按席位汇总，一眼看「每个席位有多快」。
    seats: list[SeatRateOut]


def _percentile(values: list[float], p: float) -> float | None:
    """线性插值的分位数，和 Postgres 的 `percentile_cont` 同一口径。

    在 Python 里算而不是让库算：分位数要按「一轮」取样，而一轮可以有多个模型、多
    行供给，先在 SQL 里聚完就分不出轮来了；也让这一段在 SQLite 的测试库里同样成立。
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    at = (len(ordered) - 1) * p
    low = int(at)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (at - low)


def _rate(
    output_tokens: int, started_at: datetime, stopped_at: datetime
) -> float | None:
    """一轮的速率，秒数为 0 时没有速率可言（模块 docstring 末条）。"""
    seconds = (stopped_at - started_at).total_seconds()
    if seconds <= 0:
        return None
    return output_tokens / seconds


async def _per_turn(
    session: AsyncSession, *, since: datetime, now: datetime
) -> list[dict[str, Any]]:
    """一轮 × 一个模型 × 一条供给一行：产出多少，以及这一轮自己的时长。

    连接键是 `resource_usage.turn_id` —— 用量表自己没有席位和时刻，轮次表自己没有
    产出，两边只有这一根线。

    `stopped_at IS NULL` 的轮次不算：它还没跑完，`output_tokens` 是到此刻为止的部
    分产出，除以到此刻为止的时长会把一个正在跑的轮次报成慢。
    """
    rows = (
        await session.execute(
            select(
                AgentTurn.id,
                AgentTurn.agent_handle,
                AgentTurn.started_at,
                AgentTurn.stopped_at,
                ResourceUsage.model,
                ResourceUsage.route,
                func.sum(ResourceUsage.output_tokens),
                func.coalesce(func.sum(ResourceUsage.calls), 0),
            )
            .join(ResourceUsage, ResourceUsage.turn_id == AgentTurn.id)
            .where(
                AgentTurn.started_at >= since,
                AgentTurn.started_at < now,
                AgentTurn.stopped_at.is_not(None),
                ResourceUsage.output_tokens > 0,
            )
            .group_by(
                AgentTurn.id,
                AgentTurn.agent_handle,
                AgentTurn.started_at,
                AgentTurn.stopped_at,
                ResourceUsage.model,
                ResourceUsage.route,
            )
        )
    ).all()
    return [
        {
            "turn_id": turn_id,
            "seat": agent_handle or "",
            "started_at": started_at,
            "stopped_at": stopped_at,
            "model": model,
            "supply": route,
            "output_tokens": int(tokens or 0),
            "calls": int(calls or 0),
        }
        for (
            turn_id,
            agent_handle,
            started_at,
            stopped_at,
            model,
            route,
            tokens,
            calls,
        ) in rows
    ]


def _group(
    per_turn: list[dict[str, Any]], *, window_seconds: float, key: list[str]
) -> list[SeatRateOut]:
    """把一轮一行的读数归到 `key` 说的那几列上，每组算出它的分位和吞吐。

    先按（轮次 × 这几列）合成一行：一轮用了两个模型时，在席位汇总里它是一轮，
    墙钟只算一次、速率只出一个样本 —— 不这么做，席位汇总会把一轮数成两轮，时长
    也算成两倍。在按席位 × 模型 × 供给那一层，这个合成本来就是恒等的。
    """
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in per_turn:
        identity = (str(row["turn_id"]), *(row[column] for column in key))
        mine = merged.setdefault(
            identity,
            {
                **{column: row[column] for column in key},
                "turn_id": row["turn_id"],
                "started_at": row["started_at"],
                "stopped_at": row["stopped_at"],
                "output_tokens": 0,
                "calls": 0,
            },
        )
        mine["output_tokens"] += row["output_tokens"]
        mine["calls"] += row["calls"]

    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for mine in merged.values():
        groups.setdefault(tuple(mine[column] for column in key), []).append(mine)

    out: list[SeatRateOut] = []
    for grouped, rows in sorted(groups.items()):
        # 哪几列分了组由 `key` 说；没分的列填空 —— 席位汇总那一行不按模型分。
        named = dict(zip(key, grouped, strict=True))
        rates = []
        for row in rows:
            rate = _rate(row["output_tokens"], row["started_at"], row["stopped_at"])
            if rate is not None:
                rates.append(rate)
        tokens = sum(row["output_tokens"] for row in rows)
        seconds = sum(
            (row["stopped_at"] - row["started_at"]).total_seconds() for row in rows
        )
        p50 = _percentile(rates, 0.5)
        p90 = _percentile(rates, 0.9)
        out.append(
            SeatRateOut(
                seat=named.get("seat", ""),
                model=named.get("model", ""),
                supply=named.get("supply", ""),
                turns=len(rows),
                output_tokens=tokens,
                calls=sum(row["calls"] for row in rows),
                turn_seconds=round(seconds, 3),
                p50_tps=None if p50 is None else round(p50, 4),
                p90_tps=None if p90 is None else round(p90, 4),
                window_tps=(
                    round(tokens / window_seconds, 4) if window_seconds else 0.0
                ),
            )
        )
    return out


async def seats(
    session: AsyncSession,
    *,
    window: str = "24h",
    now: datetime | None = None,
) -> dict[str, Any]:
    """这个窗口里每个席位生成得有多快，按席位 × 模型 × 供给分开，另附席位汇总。

    窗口和运行记录同一套（`run_record.admin.WINDOWS`：24h / 7d / 30d），锚在这一轮
    自己的 `started_at` 上 —— 一轮跨过窗口边界时算它开始的那一刻，和
    `run_record.turns.in_window` 一致。
    """
    now = now or datetime.now(UTC)
    span = WINDOWS.get(window, WINDOWS["24h"])
    since = now - span
    per_turn = await _per_turn(session, since=since, now=now)
    window_seconds = span.total_seconds()
    return {
        "window": window,
        "since": since.isoformat(),
        "now": now.isoformat(),
        "window_seconds": window_seconds,
        "rows": _group(
            per_turn, window_seconds=window_seconds, key=["seat", "model", "supply"]
        ),
        "seats": _group(per_turn, window_seconds=window_seconds, key=["seat"]),
    }
