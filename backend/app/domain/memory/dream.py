"""dream：空闲时对记忆做一次后台整理（照搬 Claude Code 的 autoDream，按芝士的量纲）。

人不会记得去整理记忆，所以整理要自己发生。**触发判据是「这个项目最近花了多少」**
——记忆是会话的副产品，一个项目最近写得越多，它的记忆越可能已经乱到值得梳一遍。
两个条件都满足才跑：

- 这个项目自上次整理以来累计的 `resource_usage.output_tokens` ≥ 阈值；
- 距上次整理至少 N 小时。

阈值和间隔按项目配置（`project.settings["memory_dream"]`），默认 2,000,000 /
4 小时。两个数写进 settings 而不是散在代码里，是因为它们量的是**这个项目**的
节奏：一个一天到晚在跑的代码项目和一个一周动两次的文档项目，同一个数没有意义。

**按项目一把锁。** 整理是一轮真会话，可能跑几分钟；两次整理同时起来只会互相
覆盖。锁是 `memory_dream_states.claimed_at` 上的一次比较并交换（`claim`），抢不到
就跳过这一次——不是排队，因为下一次巡检很快就到，而排队意味着一个项目的整理会
堆成队列。

**dream 自己花的不算。** 判据只数「上次整理之后」的 `output_tokens`，而收尾时
`last_dream_at` 会推进到整理结束的那一刻；另外 dream 自己那一轮记的 `kind` 是
`memory_dream`（见 `DREAM_KIND`），聚合时按前缀剔掉。两件事分开做，是因为网关的
用量是延迟落库的：一条晚到的、时间戳落在新窗口里的 dream 用量，只有 kind 认得
出来。

**这一轮删得太多就先当它没删。** 一次整理删掉某个作用域一半以上、且超过 3 条，
判为「这不像是整理，更像是那棵树出了事」，整轮作废、平台上一条都不少
（`removal_refused`）。规矩的数值和会话侧那次对账共用一份（`tree.BULK_DELETE_*`）
——两处要防的是同一件事，两个数就会漂开。

**拒绝也记一次账。** 拒绝执行的那次整理照样推进 `last_dream_at`：不推进的话，
下一次巡检会立刻再跑一遍，而一个坏掉的树会把 token 烧在一遍遍重复的拒绝上。
拒绝这件事本身记在 `memory_dream_runs` 里（`status=refused`），那是给人看的。

这个模块只管**规矩和账**（阈值、锁、拒绝判据、输入怎么压进 prompt 预算）。碰别
的领域那些读（花销、房间记录）在 `agent/chat.py` 里，那里的编排才拿得住会话；这
个领域包不 import 别人的 repository（`tests/unit/test_domain_import_guard.py`）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory.dream_prompt import DreamBriefing
from app.domain.memory.files import INDEX_NAME
from app.domain.memory.models import (
    MemoryDreamRun,
    MemoryDreamRunStatus,
    MemoryDreamState,
)
from app.domain.memory.tree import BULK_DELETE_MIN, BULK_DELETE_RATIO

#: 整理自己那一轮用量的 `kind`（`resource_usage.kind`）。带前缀地判，因为
#: `UsageRepository.add` 在不知道 token 数时会写成 `<kind>:unmetered`。
DREAM_KIND = "memory_dream"

#: 项目设置里放这两个数的键：`project.settings[DREAM_SETTINGS_KEY]`。
DREAM_SETTINGS_KEY = "memory_dream"

#: 累计到这个数才值得整理一次（输出 token）。
DEFAULT_THRESHOLD_OUTPUT_TOKENS = 10_000_000

#: 两次整理之间至少隔这么久。跑得再勤也不会更干净，只会更贵。
DEFAULT_MIN_INTERVAL = timedelta(hours=4)

#: 一把锁最多被占这么久。占着它的进程没了（重启、机器没了）时，锁自己会过期——
#: 不然一次被打断的整理会把一个项目永远锁住，而那条记录的 owner 早就没了。
CLAIM_TTL = timedelta(hours=6)

#: 阈值的时间原点：从没整理过的项目，所有的花销都算数。
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

#: 一次 prompt 里一个作用域最多带多少字。整理要的是「这些记忆讲了什么」，不是
#: 逐字复读；带了上限，它的注意力才会落在模式和重复上，而不是被一棵大树淹没。
PROMPT_SCOPE_MAX = 20_000

#: 一次整理最多带几个房间、每个房间各留多少字。
ROOMS_LIMIT = 5
ROOM_DOC_MAX = 2000
ROOM_LOG_MAX = 1200
ROOM_LOG_LINES = 30
ROOM_LINE_MAX = 200


@dataclass(frozen=True)
class DreamSettings:
    """这个项目的整理阈值，已经从 settings 里取好、带上了默认值。"""

    threshold_output_tokens: int
    min_interval: timedelta


def dream_settings(raw: dict | None) -> DreamSettings:
    """项目设置里那一段整理阈值；没配过、或者配坏了就是默认值。

    读得宽：一个写坏了的数（字符串、负数、零）退回默认值，而不是让整个项目的整理
    停掉——设置是给人改的，改坏一个字段不该静默地关掉一条后台工作。
    """
    section = raw.get(DREAM_SETTINGS_KEY) if isinstance(raw, dict) else None
    section = section if isinstance(section, dict) else {}
    threshold = section.get("threshold_output_tokens")
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold <= 0:
        threshold = DEFAULT_THRESHOLD_OUTPUT_TOKENS
    hours = section.get("min_interval_hours")
    if not isinstance(hours, (int, float)) or isinstance(hours, bool) or hours <= 0:
        interval = DEFAULT_MIN_INTERVAL
    else:
        interval = timedelta(hours=float(hours))
    return DreamSettings(threshold_output_tokens=threshold, min_interval=interval)


@dataclass(frozen=True)
class DreamDue:
    """该不该跑这一次整理，以及为什么。"""

    due: bool
    tokens: int
    reason: str


def due_decision(
    settings: DreamSettings,
    *,
    tokens: int,
    last_dream_at: datetime | None,
    now: datetime,
) -> DreamDue:
    """这个项目现在该整理记忆吗（纯函数：花销和上次整理的时刻由调用方取好）。

    「不」有几种，读的人都想知道是哪一种：还没到量、还是还没到时间。日志里分得
    开，才不会出现「整理怎么不跑」这种查不出来的问题。
    """
    if tokens < settings.threshold_output_tokens:
        return DreamDue(False, tokens, "还没到量")
    if last_dream_at is not None and now - last_dream_at < settings.min_interval:
        return DreamDue(False, tokens, "距上次整理还没到间隔")
    return DreamDue(True, tokens, "")


def since_of(state: MemoryDreamState | None) -> datetime:
    """这一次的阈值原点：上次整理的时刻；从没整理过就是 `EPOCH`。"""
    if state is None or state.last_dream_at is None:
        return EPOCH
    return state.last_dream_at


async def state_of(
    session: AsyncSession, project_id: uuid.UUID
) -> MemoryDreamState | None:
    return await session.scalar(
        select(MemoryDreamState).where(MemoryDreamState.project_id == project_id)
    )


async def claim(
    session: AsyncSession, project_id: uuid.UUID, *, now: datetime | None = None
) -> MemoryDreamState | None:
    """占住这个项目的整理锁；占不到就返回 None。

    先 upsert 一行（没有就建，有就什么都不做），再把它 `FOR UPDATE` 锁住读出来判：
    行锁把两个并发进程排成一队，读到的一定是队首那个决定之后的状态。判完立刻提交
    （调用方负责），于是这把锁只活这一句话的时间——**整理本身不占着数据库事务**，
    所以一个跑了几分钟的整理不会挡到这个项目别的事。

    `claimed_at` 过期（`CLAIM_TTL`）当成没占：被打断的一次整理不该把项目锁死。
    """
    now = now or datetime.now(UTC)
    await session.execute(
        insert(MemoryDreamState)
        .values(
            id=uuid.uuid4(),
            project_id=project_id,
            claimed_at=None,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["project_id"])
    )
    state = await session.scalar(
        select(MemoryDreamState)
        .where(MemoryDreamState.project_id == project_id)
        .with_for_update()
    )
    if state is None:  # pragma: no cover - upsert 之后一定读得到
        return None
    if state.claimed_at is not None and now - state.claimed_at < CLAIM_TTL:
        return None
    state.claimed_at = now
    await session.flush()
    return state


async def finish(
    session: AsyncSession,
    state: MemoryDreamState,
    *,
    now: datetime | None = None,
) -> None:
    """放掉锁。跑成功时把 ``last_dream_at`` 推到这一刻——它就是下一次的阈值原点。

    拒绝执行的那一次也从这里走（见模块开头）：推进了才不会一遍遍重复拒绝。
    """
    now = now or datetime.now(UTC)
    state.claimed_at = None
    state.last_dream_at = now
    await session.flush()


async def release(session: AsyncSession, state: MemoryDreamState) -> None:
    """跑失败了（没跑起来、会话答不了）也放锁，但**不**推进 ``last_dream_at``：
    这一次不算整理过，下一轮巡检该再来一次。"""
    state.claimed_at = None
    await session.flush()


def removal_refused(removed: int, total: int) -> bool:
    """这一次要删的是不是多到不像整理（`tree.BULK_DELETE_*`）。

    两个条件都过才拦：要比例，是因为小树里删一条不该触发；要条数，是因为一棵大树
    里删掉一半也可能只是几十个文件里挑了十来个。
    """
    return removed > BULK_DELETE_MIN and removed > total * BULK_DELETE_RATIO


def refused_scopes(
    before: dict[str, str], after: dict[str, str]
) -> dict[str, tuple[str, ...]]:
    """按作用域判这次整理要删的那些量。返回 ``{作用域前缀: 要删的路径}``。

    ``before`` / ``after`` 都是路径 → 正文，路径带作用域前缀（`project/x.md`）。
    只算点过名的那些：整理之后没有了、而整理之前还在的，才是这次要删的。
    """
    prefixes: dict[str, list[str]] = {}
    totals: dict[str, int] = {}
    for path in before:
        prefix = path.rsplit("/", 1)[0]
        totals[prefix] = totals.get(prefix, 0) + 1
        if path not in after:
            prefixes.setdefault(prefix, []).append(path)
    out: dict[str, tuple[str, ...]] = {}
    for prefix, paths in prefixes.items():
        if removal_refused(len(paths), totals.get(prefix, 0)):
            out[prefix] = tuple(sorted(paths))
    return out


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def trim_scopes(scopes: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """把一个项目的树压进 prompt 预算：先留索引，再按路径序留正文。

    每个作用域一个预算，按路径序取到用完为止。抽掉一条正文只损失一次「看得见
    正文」，而索引（`MEMORY.md`）永远留着——整理至少知道有这条记忆、它讲什么，
    要正文它自己去读。
    """
    out: dict[str, dict[str, str]] = {}
    for name, files in scopes.items():
        kept: dict[str, str] = {}
        used = 0
        for path in sorted(files, key=lambda p: (p != INDEX_NAME, p)):
            content = files[path]
            if path != INDEX_NAME and used + len(content) > PROMPT_SCOPE_MAX:
                continue
            kept[path] = content
            used += len(content)
        out[name] = kept
    return out


def clip(text: str, limit: int) -> str:
    """一行摘要最多留多少字（房间里那条记录、prompt 里的一段发言都用它）。"""
    return _clip(text, limit)


def briefing(
    scopes: dict[str, dict[str, str]],
    rooms: list[str],
    *,
    code_project: bool,
) -> DreamBriefing:
    """把取好的输入装成提示词要的那一份。"""
    return DreamBriefing(
        scopes=trim_scopes(scopes), rooms=rooms, code_project=code_project
    )


async def open_run(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    tokens_at_start: int,
    now: datetime | None = None,
) -> MemoryDreamRun:
    """开一条整理记录。`tokens_at_start` 是这一次判据读到的那个数——出问题时
    要能回答「它凭什么觉得该整理了」。"""
    now = now or datetime.now(UTC)
    run = MemoryDreamRun(
        project_id=project_id,
        status=MemoryDreamRunStatus.running.value,
        tokens_at_start=tokens_at_start,
        started_at=now,
        files_changed=[],
    )
    session.add(run)
    await session.flush()
    return run


async def run_of(session: AsyncSession, run_id: uuid.UUID) -> MemoryDreamRun | None:
    """按 id 取那一条整理记录。

    跑完一场整理要跨好几个短事务（判据一个、取输入一个、收尾一个），而中间夹着一
    轮可能几分钟的会话——ORM 对象跨不过那一段（每次提交都把它标成过期）。所以记
    录的 id 留着，要用的时候重读一行。
    """
    return await session.get(MemoryDreamRun, run_id)


async def close_run(
    session: AsyncSession,
    run: MemoryDreamRun,
    *,
    status: MemoryDreamRunStatus,
    summary: str,
    files: list[str] | None = None,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    run.status = status.value
    run.summary = summary
    run.finished_at = now
    if files is not None:
        run.files_changed = list(files)
    await session.flush()


__all__ = [
    "CLAIM_TTL",
    "DEFAULT_MIN_INTERVAL",
    "DEFAULT_THRESHOLD_OUTPUT_TOKENS",
    "DREAM_KIND",
    "DREAM_SETTINGS_KEY",
    "EPOCH",
    "PROMPT_SCOPE_MAX",
    "ROOMS_LIMIT",
    "ROOM_DOC_MAX",
    "ROOM_LINE_MAX",
    "ROOM_LOG_LINES",
    "ROOM_LOG_MAX",
    "DreamDue",
    "DreamSettings",
    "briefing",
    "claim",
    "clip",
    "close_run",
    "dream_settings",
    "due_decision",
    "finish",
    "open_run",
    "refused_scopes",
    "release",
    "removal_refused",
    "run_of",
    "since_of",
    "state_of",
    "trim_scopes",
]
