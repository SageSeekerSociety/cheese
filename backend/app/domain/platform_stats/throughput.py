"""每个席位生成得有多快 —— 一个能被查的数。

**口径是「窗口吞吐」**：`output_tokens ÷ 窗口秒数`，按席位 × 模型 × 供给和按席位
两处分组。它回答「这个窗口里每一组被记了多少产出、平均每秒多少」：分母是窗口，把
坐着等的空档也算进去。

**按轮速率（一轮的 `output_tokens ÷ 这一轮起止的秒数`，取 p50 / p90）2026-10-15
拆掉了。** 它把两件不是一件事的量配成一对：

- `gateway` 那一层的分子不是这一轮的产出。一个项目只有一把虚拟 key，用量是按
  **key 的日累计**读回来的，每次结算落的是「自上回结算以来整把 key 的增量」
  （`agent.gateway.drain_new_usage`），落在结算成功那一刻正好结束的那一轮上
  （`agent.gateway_usage.charge_turn_spend`）。所以一行的 token 可能是一整天全项目
  的产出，分母却是一轮的秒数。
- `subscription` 那一层一次调用一行、按调用时刻归到最近的一轮
  （`usage.subscription_ingest`），归得比网关准；但一轮里可以有并行的多次调用，
  一轮的墙钟也不是「这一轮花了多少秒在生成」。

真读数在 `docs/evidence/seat-tps-2026-10-15.md`：30 天窗口里有 7 行
`output_tokens ÷ calls` 是 29.6 万 ~ 47.4 万（单次调用吐不出这么多，说明这两个计数
不是同一批调用），占那个窗口总量的 28.8%；24h 的席位层 p90 报到 12,139 token/秒。
**换口径就是换了**：`p50_tps` / `p90_tps` 两个字段不再存在，读到旧表上这两个数不要
用 —— 那是对上面这种配对的分布取的分位。

**查不到的说「查不到」。** 逐轮的 token 从任何接口都取不到（`/run-records/turns`
只有起止时刻），所以「哪些轮是灌出来的」报不出名字，只能报到组。`turn_seconds` 是
这一组所有轮的墙钟合计，**不是速度的分母**，留着当背景量：配 `turns` / `calls` 读，
轮数和调用数都正常而 `window_tps` 低，是这一组在跑但慢了；轮数少，是它没在跑。

**网关那一层的席位归属是近似的。** 结算落在哪一轮上，取决于结算成功那一刻谁在跑，
所以席位之间会串味。`(模型 × 供给)` 一层的合计是账本的原数，席位那一层不是。

**`output_tokens = 0` 的行不算。** 那不是「跑过但没数」，是这一轮没拿到用量时按席位
配置补写的标记：`model` 写席位设的模型、`route` 写当轮路由，都不代表实际服务它的
模型（见团队记忆《直接查平台库时有四处会骗人》第 4 条）。放进来会造出一批假的产出
和假的模型归属，2026-10-04 数出过 131 条假 opus 记录。判实跑只看 `output_tokens > 0`。

**供给分开列。** `route` 是流量实际走的那条道（`gateway` / `subscription` /
`native` / `compute`，老行是空串）。分开列，被限流的席位才看得出是速率掉了，
不是没在跑。

**座席是 `agent_handle`，不是房间。** 一个房间并排坐着几个队友，`agent_turns` 上的
`agent_handle` 才是这一轮是谁在生成（`None` 是没人认领的一轮：会话自己起的，或者
装配前就没了）。用量表自己没有席位，只能从轮次这边接过来。
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
    """一行 = 一个席位 × 一个模型 × 一条供给上的产出和窗口吞吐。"""

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
    #: 这些轮的墙钟秒数合计。**不是速率的分母**（按轮速率已拆，见模块 docstring），
    #: 留作背景量：它说明这一组有多少轮内时间。
    turn_seconds: float
    #: 窗口吞吐：`output_tokens ÷ 窗口秒数`，把坐着等的空档也算进分母。配 `turns` /
    #: `calls` 读：那两个正常而它低，是这一组在跑但慢了；轮数少，是它没在跑。
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
    """把一轮一行的读数归到 `key` 说的那几列上，每组算出它的产出和窗口吞吐。

    先按（轮次 × 这几列）合成一行：一轮用了两个模型时，在席位汇总里它是一轮，
    墙钟只算一次 —— 不这么做，席位汇总会把一轮数成两轮，时长也算成两倍。在按席位
    × 模型 × 供给那一层，这个合成本来就是恒等的。
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
        tokens = sum(row["output_tokens"] for row in rows)
        seconds = sum(
            (row["stopped_at"] - row["started_at"]).total_seconds() for row in rows
        )
        out.append(
            SeatRateOut(
                seat=named.get("seat", ""),
                model=named.get("model", ""),
                supply=named.get("supply", ""),
                turns=len(rows),
                output_tokens=tokens,
                calls=sum(row["calls"] for row in rows),
                turn_seconds=round(seconds, 3),
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
    """这个窗口里每个席位产出得有多快，按席位 × 模型 × 供给分开，另附席位汇总。

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
