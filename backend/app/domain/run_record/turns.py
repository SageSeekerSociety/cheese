"""一轮一行：这个窗口里跑过哪些轮次、各自怎么了。

**为什么要有它。** 运行记录（`admin.py`）是按「一种事一行」记的：同一处代码、同
一种异常在这一窗口里出了几次。那个读法答不了「一共跑了多少轮、其中多少轮没跑完」
—— 分母不在里面。分母在 `agent_turns`：一行一轮，行是关掉不是删掉
（`agent.models.AgentTurn`），所以任何窗口问得出来。

**失败的身份只在 `blocks.meta` 里。** 一轮失败时，平台在房间里落一条事件
（话是 `agent/platform_notices.py` 说的，码在 `notice_meta`），`meta.event_type`
是该类别码，`blocks.turn_id` 是这一轮的号 —— 前提是调用方把轮次号传给了
`post_system_event`。所以「这一轮有没有失败」= 有没有一条 `turn_id` 指着它的
终态事件。

**类别码不是全部：`severity=info` 的那一条不是失败。** 平台把 `turn_failed` 这个码
也用在几条「没事，只是要等」的提示上 —— 主人不在线（`initial_admission`）、这一轮
在等人（`turn/intake/completion` 的 waiting）、消息没送到任何人
（`own_calls.say_refused`），它们和真正的失败共用一个类别码，只在 `meta.severity`
上分得开。所以这一页跳过 `severity=info` 的事件：不跳，一条健康、还在跑的轮次会被
报成失败，虚高这一页存在的理由（分母）。

**「没有失败事件」是没记录，不是记了成功。** 成功不写事件。这个读法因此只能证明
「平台没有为这一轮落过终态失败事件」，不能证明这一轮做了什么 —— 一个平台的提示
没有说出真正的原因、调用方漏传轮次号、或者事件根本没落住（`post_system_event`
的每个调用点都是 best-effort），都会让一轮看起来是干净的。

已知有几条失败在本页读不到，因为它们落事件时没有轮次号可传：

* 卡死轮次的超时（`runtime._post_orphan_event`，一个房间一条，不给轮次号）；
* 孤儿扫底认下的部署中断（同一个函数，同样不给轮次号）；
* 投递彻底放弃（`delivery.timer` 的 `deliveryGaveUp`，同样只给房间）。

这一页把它们算成没有失败事件。要数它们，去看 `run_records` 那一层。

**两张表是字面读的，不是 import 来的。** 这一层在 `app.domain` 的最底下，`agent`
和 `block` 都在它上面，往上 import 就是反着依赖（`.importlinter` 的 C5）。所以这
里像 `admin.py` 读 `projects` 那样，把要用的几列自己写出来（`table()` /
`column()`），让这一层不欠上面的人情。失败类别码和 meta 键同理，逐字写在这里，
各自的家写在注释里 —— 改了那边要一并改这里，正文里的语义也归那边解释。
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Uuid,
    cast,
    column,
    func,
    select,
    table,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.run_record.admin import WINDOWS
from app.domain.run_record.repository import RunRecordRepository

#: 一轮一行（`agent.models.AgentTurn`，表 `agent_turns`）。只读这四列。
_turns = table(
    "agent_turns",
    column("id", Uuid),
    column("conversation_id", Uuid),
    column("started_at", DateTime(timezone=True)),
    column("stopped_at", DateTime(timezone=True)),
    column("stop_reason", String(32)),
)

#: 房间里的块（`block.models.Block`，表 `blocks`）。失败事件和提示计数都落在这。
_blocks = table(
    "blocks",
    column("id", Uuid),
    column("turn_id", Uuid),
    column("meta", JSON),
    column("created_at", DateTime(timezone=True)),
)

#: 平台为「这一轮没有把话说完」落下的类别码，和 `notice_meta` 的
#: `EVENT_*` 逐字相同。`turn_failed` 是平台认不出的失败（缺陷信号，不重试）；
#: `platform_error` 是平台认得的故障（带 `code`，见 `agent.platform_failures`）；
#: `turn_timeout` 是一个字都没等到；`deploy_interrupted` 是部署把轮次掐了（孤儿
#: 轮次扫底）。
#:
#: 这比 `block.indexed_rows.FAILED_TURN_EVENTS` 宽：那里要的是「房间该不该摆一张
#: 红卡」，超时和部署中断是平台自己扛过去的警告，所以不算。这里要的是「这一轮跑完
#: 了没有」，超时和部署中断当然没跑完 —— 少算它们，这一页就会把一问三不知的一轮
#: 报成成功。
#:
#: 后两类多数读不到（见模块 docstring）：带轮次号的超时（一个字都没等到的）读得到，
#: 孤儿扫底那几条代不进轮次号。留着这两个码，是因为口径说的是「终态事件」，不是
#: 「今天恰好对得上的那几种」。
TURN_FAILURE_EVENTS: tuple[str, ...] = (
    "turn_failed",
    "turn_timeout",
    "deploy_interrupted",
    "platform_error",
)

#: `blocks.meta` 上的两个键，和 `block.models` 的 `PROMPTED_TURN_META_KEY` /
#: `PROMPT_ATTEMPTS_META_KEY` 逐字相同。
PROMPTED_TURN_META_KEY = "prompted_turn"
PROMPT_ATTEMPTS_META_KEY = "prompt_attempts"

#: `blocks.meta.severity` 的值之一，和 `notice_meta` 的 `SEVERITY_INFO`
#: 逐字相同。事件带这个 severity 时它是提示不是失败 —— 理由见模块 docstring。
#: 没有这个键的老行照算失败：它们不是平台写的提示。
NOT_A_FAILURE_SEVERITY = "info"

DEFAULT_LIMIT = 200
MAX_LIMIT = 500


class TurnOut(BaseModel):
    """一轮一行。"""

    turn_id: str
    conversation_id: str
    started_at: str
    #: 空 = 这一轮还开着（`stopped_at IS NULL` 就是「在跑」），不是「不知道」。
    stopped_at: str | None
    #: 谁让这一轮停的（`agent.models.STOP_*`），由关掉它的那段代码写下。
    #: 空有两种来路：这一列加上之前就结束的行，或者这一轮还没结束。
    stop_reason: str | None = None
    #: 这一轮的终态失败事件类别，或者空 —— 空是「没记录过失败」，或者这一轮只落了
    #: 提示（`severity=info`），见模块 docstring。
    failure_event: str | None
    #: 这一轮的提示被投了几次，或者空。
    #:
    #: 取的是喂给这一轮的那批消息块上 `meta.prompt_attempts` 的最大值（这个数在
    #: 组提示的时候加一，不在轮次结束时加 —— 一轮死了不留痕迹，下一轮会把同一批
    #: 原样再喂一次，这个计数器就是那道痕迹）。所以它是**这一批到这一轮为止一共
    #: 被喂了几次**，不是「这一轮喂了几次」。
    #:
    #: 空有两种来路：这一轮没有消息块指向它（平台自己起的一轮、或者没有待投的
    #: 消息），或者这一轮失败、它的认领在结束时被撤回了
    #: （`block.repositories.BlockRepository.forget_prompted_turn`）—— 失败的那一轮
    #: 恰恰最常读回空。批次的总次数还留在消息块上，但没有指针指回这一轮。
    prompt_attempts: int | None = None
    #: 这一轮的会话最近发出一帧的种类，和那是多久以前。只由路由补在**还没结束**的
    #: 轮次上（见 `api.routes.run_records._add_frames`）；空 = 不知道，不是 0 ——
    #: 本进程不读这个会话，或者跑它的进程已经不在了，都是不知道。
    last_frame_kind: str | None = None
    last_frame_age_s: float | None = None


class TurnListOut(BaseModel):
    window: str
    since: str
    now: str
    limit: int
    #: 这一窗口里一共有多少轮 —— 这一页只是它前面的 `limit` 行。
    total: int
    turns: list[TurnOut]


class TurnRecordOut(BaseModel):
    """这一轮落到运行记录里的一件事（`run_records` 的一行）。"""

    id: str
    kind: str
    severity: str
    content: str
    at: str


class TurnDetailOut(TurnOut):
    """一轮的下钻：它怎么了，和它落到运行记录里的那几件事。"""

    #: `failed` = 有一条终态失败事件；`unfinished` = 没有。
    #:
    #: **`stopped_at` 有值也不代表成功。** 成功不写事件（模块 docstring），这个
    #: 读法只能证明平台没有为它落过终态失败事件，不能证明它做完了什么。所以没有
    #: 事件时这里说的是 `unfinished`，不是 `done`。
    status: Literal["failed", "unfinished"]
    records: list[TurnRecordOut]


def _event_type():
    return _blocks.c.meta["event_type"].as_string()


def _severity():
    return _blocks.c.meta["severity"].as_string()


async def _failure_events(
    session: AsyncSession, turn_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """每一轮落过的终态失败事件，取最后一条。

    同一轮落两条是可能的（比如超时之后平台又认了一次故障），报最后一条：那是这一轮
    到结束时平台最后说的话。落在同一刻的按块 id 排，让两次读拿到同一个答案。

    `severity=info` 的那几条不是失败，跳过（模块 docstring 说的共用类别码）；
    排在它们前面的失败事件因此不会被它们盖掉。
    """
    if not turn_ids:
        return {}
    kind = _event_type()
    severity = _severity()
    rows = (
        await session.execute(
            select(_blocks.c.turn_id, kind, severity)
            .where(_blocks.c.turn_id.in_(turn_ids), kind.in_(TURN_FAILURE_EVENTS))
            .order_by(_blocks.c.created_at.asc(), _blocks.c.id.asc())
        )
    ).all()
    return {
        turn_id: event
        for turn_id, event, level in rows
        if level != NOT_A_FAILURE_SEVERITY
    }


async def _prompt_attempts(
    session: AsyncSession, turn_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """每一轮的提示被投了几次，按 `meta.prompted_turn` 认领回来。

    计数写在**消息块**上（不是轮次行上），而且每次组提示都换一次认领：`prompted_turn`
    被改写成当前这一轮。所以同一批消息只有它最后被喂给的那一轮数得出来。
    """
    if not turn_ids:
        return {}
    pointed = _blocks.c.meta[PROMPTED_TURN_META_KEY].as_string()
    attempts = cast(_blocks.c.meta[PROMPT_ATTEMPTS_META_KEY].as_string(), Integer)
    rows = (
        await session.execute(
            select(pointed, func.max(attempts))
            .where(pointed.in_([str(turn_id) for turn_id in turn_ids]))
            .group_by(pointed)
        )
    ).all()
    found: dict[uuid.UUID, int] = {}
    for pointed_at, count in rows:
        if count is None:
            continue
        try:
            found[uuid.UUID(pointed_at)] = int(count)
        except ValueError:  # 认领不是个轮次号 —— 不是这个读法的东西，跳过
            continue
    return found


async def of_turn(session: AsyncSession, turn_id: uuid.UUID) -> dict[str, Any] | None:
    """One turn by its full id, with what it left in the run records.

    Reuses the same two reads as the window (`_failure_events`,
    `_prompt_attempts`) so a turn's failure identity is answered the same way
    whether it is found in a list or asked for on its own. ``None`` means
    `agent_turns` has no row with this id — not that it never ran: rows are
    closed, not deleted, so a turn from outside every retention window is
    still found here.
    """
    row = (
        await session.execute(
            select(
                _turns.c.id,
                _turns.c.conversation_id,
                _turns.c.started_at,
                _turns.c.stopped_at,
                _turns.c.stop_reason,
            ).where(_turns.c.id == turn_id)
        )
    ).one_or_none()
    if row is None:
        return None
    failures = await _failure_events(session, [turn_id])
    attempts = await _prompt_attempts(session, [turn_id])
    failure = failures.get(turn_id)
    records = await RunRecordRepository(session).of_turns([turn_id])
    return {
        "turn_id": str(row.id),
        "conversation_id": str(row.conversation_id),
        "started_at": row.started_at.isoformat(),
        "stopped_at": row.stopped_at.isoformat() if row.stopped_at else None,
        "stop_reason": row.stop_reason,
        "failure_event": failure,
        # No terminal failure event is "nothing was recorded", not success.
        # `stopped_at` is when the row closed, not evidence it finished: the
        # only two answers this read gives are `failed` and `unfinished`.
        "status": "failed" if failure else "unfinished",
        "prompt_attempts": attempts.get(turn_id),
        "records": [
            {
                "id": str(record.id),
                "kind": record.kind,
                "severity": record.severity,
                "content": record.content,
                "at": record.created_at.isoformat(),
            }
            for record in records
        ],
    }


async def in_window(
    session: AsyncSession,
    *,
    window: str = "24h",
    limit: int = DEFAULT_LIMIT,
    now: datetime | None = None,
) -> dict[str, Any]:
    """这一窗口里跑过的轮次，新的在前。`limit` 之外的行只算进 `total`。

    窗口跟运行记录同一套（`admin.WINDOWS`：24h / 7d / 30d），锚在这一轮自己的
    `started_at` 上 —— 一轮跨过窗口边界时算它开始的那一刻，不算它结束的那一刻：
    分母的问题是「这段时间起了多少轮」。
    """
    now = now or datetime.now(UTC)
    span = WINDOWS.get(window, WINDOWS["24h"])
    since = now - span
    within = (_turns.c.started_at >= since, _turns.c.started_at < now)
    total = await session.scalar(
        select(func.count()).select_from(_turns).where(*within)
    )
    page = (
        await session.execute(
            select(
                _turns.c.id,
                _turns.c.conversation_id,
                _turns.c.started_at,
                _turns.c.stopped_at,
                _turns.c.stop_reason,
            )
            .where(*within)
            .order_by(_turns.c.started_at.desc(), _turns.c.id.desc())
            .limit(limit)
        )
    ).all()
    turn_ids = [turn_id for turn_id, *_ in page]
    failures = await _failure_events(session, turn_ids)
    attempts = await _prompt_attempts(session, turn_ids)
    return {
        "window": window,
        "since": since.isoformat(),
        "now": now.isoformat(),
        "limit": limit,
        "total": total or 0,
        "turns": [
            {
                "turn_id": str(turn_id),
                "conversation_id": str(conversation_id),
                "started_at": started_at.isoformat(),
                "stopped_at": stopped_at.isoformat() if stopped_at else None,
                "stop_reason": stop_reason,
                "failure_event": failures.get(turn_id),
                "prompt_attempts": attempts.get(turn_id),
            }
            for turn_id, conversation_id, started_at, stopped_at, stop_reason in page
        ],
    }
