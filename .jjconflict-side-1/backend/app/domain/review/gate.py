"""机器闸门 background runner (spec §4.4/§9, eval C2).

采纳即合并退役闸门 (docs/accept-is-merge.md #296, stage 1): THIS RUNNER IS NO
LONGER DISPATCHED. `AcceptService.create_card` never mints a `pending_gate`
card any more (a card is the view of a PR; real CI on that PR decides, not a
private platform check), and `routes/accept.py` no longer calls `dispatch`.
The module stays only because `review/gate_sweep.py` still needs its constants
and `in_flight_card_ids()` to clean up any HISTORICAL `pending_gate` rows left
in the DB from before the retirement — that in-flight set is now always empty,
which is exactly what lets the sweep condemn every stale row it finds. A guard
test (`test_accept_gate.py`) pins that filing a card can no longer reach
`pending_gate`. Do not re-wire `dispatch`; everything below documents the
retired behaviour for the historical rows the sweep still handles.

Historically: filing an accept card on a project with a `check_command` created
the card in `pending_gate` and dispatched this runner. It ran the command in the
topic's workspace (host-side, see workspace.service.run_check_command for the
trust model), then settled the card: green → pending (the reviewer only ever saw
a green card), red → gate_failed + a system nudge so 芝士 went and fixed it.

Third outcome (2026-08-11): the check may not run at all — no usable toolchain
in the gate container, Docker refusing to start. `check_command` reports that as
exit code 2 (check.sh --strict, which run_check_command turns on) and the card
goes to gate_blocked. It is deliberately NOT green: a check that never ran says
nothing about the code, and the whole point of a gate is that it actually ran.

The check can take minutes, so it never runs inside a request handler — the
POST returns immediately with the pending_gate card and the UI polls.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.review import pr_publish
from app.domain.review.models import GateOutcome
from app.domain.workspace import service as ws

if TYPE_CHECKING:
    from app.domain.review.services import AcceptService

logger = logging.getLogger("cheesex.gate")

# Hard ceiling for a project's check command (10 minutes).
GATE_TIMEOUT_S = 600
# Exit code a check command uses to say "I couldn't run" (as opposed to "the
# code is bad"). Convention shared with .claude/scripts/check.sh --strict; a
# command that doesn't know about it simply never returns 2 and nothing changes.
BLOCKED_EXIT_CODE = 2
# Where full check output lands: logs/gate-<topic8>.log (project-local ./logs).
LOG_DIR = Path("logs")

# Keep references so in-flight gate tasks aren't GC'd (same pattern as
# TurnRunner._tasks). Keyed by card so the sweeper can ask "is THIS card's gate
# still running in this process?" — see gate_sweep.sweep_abandoned_gate_cards.
# The map is per-process and empty right after a restart, which is exactly the
# state that makes the startup sweep able to condemn everything it finds.
_INFLIGHT: dict[uuid.UUID, asyncio.Task] = {}

# `_settle` (and `_mark_started`) can arrive before the card's INSERT is
# visible: it commits in the request that dispatched us, which may still be
# finishing when a very fast check gets here. Retry for ~10s.
_CARD_VISIBLE_TRIES = 100
_CARD_VISIBLE_DELAY_S = 0.1


def in_flight_card_ids() -> frozenset[uuid.UUID]:
    """Cards whose gate is genuinely still running in THIS process."""
    return frozenset(_INFLIGHT)


def dispatch(
    session_factory: async_sessionmaker,
    chat_service,
    runner,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
    command: str,
) -> None:
    """Start the gate check in the background; returns immediately."""
    task = asyncio.create_task(
        _run(
            session_factory,
            chat_service,
            runner,
            card_id=card_id,
            topic_id=topic_id,
            project_id=project_id,
            command=command,
        )
    )
    _INFLIGHT[card_id] = task
    task.add_done_callback(lambda _t, cid=card_id: _INFLIGHT.pop(cid, None))


async def _run(
    session_factory: async_sessionmaker,
    chat_service,
    runner,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
    command: str,
) -> None:
    log_path = LOG_DIR / f"gate-{topic_id.hex[:8]}.log"
    try:
        # Both resolve-worktree and the check itself block — keep them off-loop.
        worktree = await asyncio.to_thread(ws.topic_worktree, project_id, topic_id)
        # 打点：检查这就要开跑了 (孤儿卡诊断, 2026-08-11). Everything above this
        # line is queueing + worktree preparation, so a stale `pending_gate`
        # card whose `gate_started_at` is still NULL never got this far.
        await _mark_started(session_factory, card_id=card_id)
        result = await asyncio.to_thread(
            ws.run_check_command,
            worktree,
            command,
            timeout=GATE_TIMEOUT_S,
            log_path=log_path,
        )
    except Exception as exc:  # noqa: BLE001 — a broken gate must fail the card, not hang it
        logger.exception("gate check failed to run for card %s", card_id)
        result = {"exit_code": -1, "tail": f"检查无法执行：{exc}"}

    exit_code = result["exit_code"]
    # -1 is "we never got the command started" (Docker missing, worktree gone) —
    # the same fact as exit 2, established one layer further out.
    if exit_code == 0:
        outcome = GateOutcome.passed
    elif exit_code in (BLOCKED_EXIT_CODE, -1):
        outcome = GateOutcome.blocked
    else:
        outcome = GateOutcome.failed
    passed = outcome == GateOutcome.passed
    tail = str(result["tail"])
    settled = await _settle(
        session_factory, card_id=card_id, outcome=outcome, tail=tail
    )
    logger.info(
        "gate %s for card %s (exit %s, log %s)",
        outcome.value,
        card_id,
        exit_code,
        log_path,
    )

    if not settled:
        # The card left `pending_gate` while we were running — swept as
        # abandoned, or voided by a human. Neither the PR nor the 修一修 nudge
        # applies to a card that is already closed.
        return

    if passed and pr_publish.enabled():
        # PR-based accept (#188 §5.1): a green gate is when the card reaches
        # the reviewer — open its PR now so CI runs while the card waits.
        pr_publish.dispatch(
            session_factory,
            card_id=card_id,
            topic_id=topic_id,
            project_id=project_id,
        )

    if not passed:
        # 红了 / 没跑成 → 芝士收到系统 nudge（same dispatch pattern as the accept
        # merge-conflict resolve turn in routes/accept.py）。两种情况要说不同的
        # 话：红了是去改代码，没跑成是去把检查环境弄起来 —— 让芝士对着一份
        # 「什么都没跑」的输出找 bug，只会浪费一整轮并且很可能原样再递一次。
        if outcome == GateOutcome.blocked:
            content = (
                "你递的验收卡没有送到验收人手上：平台的质量检查**没能跑起来**"
                "（不是没通过——它对你的代码没有任何结论）。"
                f"检查输出的结尾如下：\n```\n{tail[-1500:]}\n```\n"
                f"完整输出在 {log_path}。门禁容器**没有网络**，只能用你工作区里"
                "已经装好的环境：如果是 Python 工具链缺失，先在 `backend/` 里"
                "`uv sync` 把 .venv 装好，再重新递卡。"
                "如果不是这个原因、或者装好后依然跑不起来，**别反复重试**，"
                "在对话里把情况说清楚交给人处理。"
            )
        else:
            content = (
                "你递的验收卡没有通过平台的质量检查，卡片没有送到验收人手上。"
                f"检查输出的结尾如下：\n```\n{tail[-1500:]}\n```\n"
                f"完整输出在 {log_path}。请在工作区里修复这些问题，"
                "跑一遍同样的检查确认全绿，然后重新递验收卡。"
            )
        runner.submit(
            chat_service,
            topic_id,
            author="system",
            content=content,
            summon=True,
        )


async def _once_visible(
    session_factory: async_sessionmaker,
    card_id: uuid.UUID,
    action: Callable[["AcceptService"], Awaitable[object]],
) -> bool:
    """Run ``action`` against the card as soon as its row is visible, then
    commit. Returns False if it never showed up within the retry window.

    The wait exists because the card's INSERT commits in the request that
    dispatched the gate, which may still be finishing when a very fast check
    (a no-op test command) gets here.
    """
    from app.domain.review.repositories import AcceptCardRepository
    from app.domain.review.services import AcceptService

    for _ in range(_CARD_VISIBLE_TRIES):
        async with session_factory() as session:
            if await AcceptCardRepository(session).get(card_id) is not None:
                await action(AcceptService(session))
                await session.commit()
                return True
        await asyncio.sleep(_CARD_VISIBLE_DELAY_S)
    return False


async def _mark_started(
    session_factory: async_sessionmaker, *, card_id: uuid.UUID
) -> None:
    """Record that the check command is about to run. Best effort by design: a
    missing timestamp costs diagnostics, never the gate result — so it must not
    be able to fail the check that is about to run."""
    try:
        ok = await _once_visible(
            session_factory,
            card_id,
            lambda svc: svc.mark_gate_started(card_id=card_id),
        )
    except Exception:  # noqa: BLE001 — 打点失败不能拖垮闸门本身
        logger.exception("gate start timestamp not recorded for card %s", card_id)
        return
    if not ok:
        logger.error("gate start not recorded: card %s never became visible", card_id)


async def _settle(
    session_factory: async_sessionmaker,
    *,
    card_id: uuid.UUID,
    outcome: GateOutcome,
    tail: str,
) -> bool:
    """Persist the gate result. False = it did not land (and won't)."""
    from app.core.errors import ValidationError

    try:
        ok = await _once_visible(
            session_factory,
            card_id,
            lambda svc: svc.finish_gate(
                card_id=card_id, outcome=outcome, output_tail=tail
            ),
        )
    except ValidationError:
        # The card is no longer waiting on the gate: gate_sweep condemned it as
        # abandoned, or a human voided it. Both are deliberate closures — a late
        # result must not reopen or overwrite them.
        logger.warning(
            "gate result ignored: card %s is no longer pending_gate", card_id
        )
        return False
    if not ok:
        # 孤儿卡的第二条产生路径 (2026-08-11): giving up here used to strand the
        # card in `pending_gate` forever. It still gives up — there is nothing
        # sane left to do in-process — but gate_sweep now condemns whatever this
        # leaves behind, so the topic no longer deadlocks on it.
        logger.error("gate result dropped: card %s never became visible", card_id)
    return ok
