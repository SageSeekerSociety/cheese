"""后台任务跑完 → 唤醒话题（"等长任务 ≠ 人间蒸发"）。

An agent that has to wait on a multi-minute command (a full check run, a build)
used to end its turn, and the topic froze: nothing on the platform brings a turn
back when a detached process exits — comments do (``runner.submit(summon=True)``)
but ``webhook.post_with_retries`` deliberately only lands a block, and the Claude
Code hook path (``/sandbox/hooks/{topic}``) parks events with no listener instead
of summoning. So a human had to notice and poke the topic.

``cheese await "<cmd>"`` closes that hole in two halves:

1. **register** — the CLI tells us the command it is about to run and gets back a
   task id plus a wake token whose lifetime covers the whole run (the container's
   own ``CHEESE_TOKEN`` expires in an hour; these tasks are longer than that).
2. **report** — the command's detached child posts the exit code and output tail
   back here when it exits. The result ALWAYS lands in the timeline; whether it
   also summons a turn is decided by the four guards below.

The command itself runs in the agent's own sandbox, not here: the platform's
other background runner (``review.gate``) uses a disposable no-network container,
which is exactly why a full check there costs tens of minutes, and ``docker exec``
would weld this feature to the tmux/docker compute backend.

Guards (all four are here, not in the CLI — a sandbox-side check is advisory):

* **唤醒风暴** — a topic gets at most ``MAX_WAKES_PER_WINDOW`` automatic wakes per
  rolling ``WAKE_WINDOW_S``, and at most ``MAX_ACTIVE_PER_TOPIC`` registered tasks
  at once. Over the line, the result still lands as a block; only the summon is
  withheld.
* **话题正在跑** — a running turn is not interrupted; the wake is DEFERRED until
  the topic goes idle (bounded by ``DEFER_CEILING_S``), not dropped.
* **上下文** — the wake carries command, exit code, duration, log path and the
  tail of the output (same shape as the gate's red-card nudge), so the agent never
  has to guess what finished.
* **归档 / 卡已结算** — an archived topic or a settled accept card takes the result
  as a block and nothing else.

It also owns the other half of "a command outlives the turn": while one is in
flight the topic's automatic snapshot is HELD (``checkpoint_worktree`` below).
"""

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.errors import ConflictError, ValidationError
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.topic.models import Topic, TopicStatus
from app.domain.webhook import service as webhook_service
from app.domain.workspace import service as ws

logger = logging.getLogger("cheesex.await")

# How many await tasks one topic may have in flight at once.
MAX_ACTIVE_PER_TOPIC = 3
# Automatic wakes allowed per topic per rolling window — the storm brake. A woken
# agent can start another background task; without a ceiling that is a free-running
# loop that spends credits with nobody watching.
MAX_WAKES_PER_WINDOW = 6
WAKE_WINDOW_S = 3600.0
# A wake that arrives while the topic is running waits for it to go idle. Bounded:
# a turn that never ends must not keep a task alive forever.
DEFER_CEILING_S = 900.0
DEFER_POLL_S = 3.0
# Longest command we accept, and the longest tail we carry into the wake prompt
# (the gate's red-card nudge uses the same 1500).
MAX_TIMEOUT_S = 6 * 3600
TAIL_LIMIT = 1500
# How long past its own timeout a task keeps holding the snapshot. The child
# kills the command at `timeout_s` and reports right after, so anything still
# registered past that is a child that died with its container — and a dead child
# must not freeze a topic's version history forever.
SNAPSHOT_HOLD_GRACE_S = 60.0

# Terminal accept-card states: the work has been settled, so a late background
# result is history, not something to wake anyone about.
_SETTLED_CARD_STATES = (
    AcceptStatus.accepted,
    AcceptStatus.rejected,
    AcceptStatus.revoked,
)


@dataclass
class AwaitedTask:
    """One backgrounded command the platform has promised to wake the topic for."""

    id: uuid.UUID
    project_id: uuid.UUID
    topic_id: uuid.UUID
    command: str
    label: str
    timeout_s: int
    log_path: str
    started_at: float = field(default_factory=time.time)


# Process-global, like TurnRunner._tasks and HookRouter: a restart forgets
# in-flight tasks. For the WAKE half that costs one wake (the child's report 404s
# and it gives up) and corrupts nothing, and persisting would not buy much — a
# container rebuild kills the child that was going to report anyway.
#
# For the SNAPSHOT-HOLD half it is worse, and the container-rebuild argument does
# not transfer: the child runs in the agent's own sandbox, so a BACKEND restart
# leaves it alive and still writing the worktree while every hold is forgotten —
# the next checkpoint then tears the tree exactly as before. Strictly better than
# no hold at all (was: always torn; now: torn only if a restart lands inside the
# window) and not worth blocking on, but real: this topic itself was interrupted
# by a platform restart twice on 2026-08-11. If it needs fixing, the fix is to
# PERSIST the hold — widening SNAPSHOT_HOLD_GRACE_S does nothing for a restart and
# only lets a dead child freeze history longer (裁定 2026-08-11, PR 采纳意见).
_ACTIVE: dict[uuid.UUID, AwaitedTask] = {}
# topic id → timestamps of recent automatic wakes (rolling window).
_WAKES: dict[str, deque[float]] = {}
# Deferred-wake tasks, kept referenced so they aren't GC'd mid-wait.
_DEFERRED: set[asyncio.Task] = set()


def reset() -> None:
    """Drop all in-memory state. For tests — each one starts from a clean slate."""
    _ACTIVE.clear()
    _WAKES.clear()


def active_for_topic(topic_id: uuid.UUID) -> list[AwaitedTask]:
    return [t for t in _ACTIVE.values() if t.topic_id == topic_id]


def get(task_id: uuid.UUID) -> AwaitedTask | None:
    return _ACTIVE.get(task_id)


def register(
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    command: str,
    label: str,
    timeout_s: int,
    log_path: str,
) -> AwaitedTask:
    """Record a command the sandbox is about to run in the background.

    Raises ValidationError on a malformed request and ConflictError when the topic
    already has ``MAX_ACTIVE_PER_TOPIC`` tasks in flight (the first storm brake —
    an agent that fires off background work in a loop is stopped at registration
    rather than at wake time)."""
    if not command.strip():
        raise ValidationError("command 不能为空")
    if timeout_s <= 0 or timeout_s > MAX_TIMEOUT_S:
        raise ValidationError(f"timeout_s 必须在 1..{MAX_TIMEOUT_S} 之间")
    if len(active_for_topic(topic_id)) >= MAX_ACTIVE_PER_TOPIC:
        raise ConflictError(
            f"本话题已有 {MAX_ACTIVE_PER_TOPIC} 个后台任务在跑，先等它们跑完"
        )
    task = AwaitedTask(
        id=uuid.uuid4(),
        project_id=project_id,
        topic_id=topic_id,
        command=command,
        label=label.strip() or command.strip()[:60],
        timeout_s=timeout_s,
        log_path=log_path,
    )
    _ACTIVE[task.id] = task
    logger.info("await task %s registered for topic %s", task.id, topic_id)
    return task


def forget(task_id: uuid.UUID) -> None:
    _ACTIVE.pop(task_id, None)


# --- 快照撞上长命令 ---------------------------------------------------------
#
# The automatic snapshot fires when a turn ENDS (`provider.checkpoint` →
# `ws.snapshot_worktree`), and `cheese await` exists precisely so a turn CAN end
# while its command keeps running. The two windows overlap by construction, not
# by bad luck — and a snapshot taken mid-command freezes whatever half-written
# state the worktree is in into a commit. Every downstream reader (话题分支,
# diff, 验收卡, 复核的人) reads commits, not the worktree, so the worktree healing
# 35 seconds later fixes nothing. Measured 2026-08-11: a verification script
# deleted the line it was validating, ran a negative control, then restored it;
# the snapshot landed 10s in, and the branch showed the fix missing for 2 hours.
#
# Policy: HOLD the automatic snapshot while a task is in flight, then take one as
# soon as the last task reports.
#
# The reason to prefer holding is NOT mainly that it is the recoverable option
# (it is — the worktree is a host bind mount, so nothing is lost and the next
# snapshot picks it up, whereas a torn commit stays in history with the bookmark
# pointing at it for the whole run). It is what each failure LOOKS like:
#
# * a held snapshot produces a QUESTION — "why isn't my change on the branch?" —
#   which somebody asks and somebody answers;
# * a torn commit produces a CONFIDENT WRONG CONCLUSION — "this fix was never
#   made" — which nobody re-checks, because it looks completely normal.
#
# The incident is the proof: those 2 hours were not spent failing to recover, they
# were spent with nobody suspecting anything. So this choice holds even where both
# options are recoverable. Same principle as 宁可重复不可丢失 on the message path:
# make the failure mode the visible one (裁定 2026-08-11, PR 采纳意见).
#
# Which is also why the hold itself must be visible — `status_snapshot` puts it on
# `cheese status`, or "my edits aren't on the branch" becomes the same mystery by
# another route. Snapshots that CANNOT be held (采纳前快照 and friends: a human is
# waiting, and refusing would wedge the accept) instead get a warning marker in
# their commit message — see `ws.snapshot_worktree`.


def snapshot_hold(topic_id: uuid.UUID) -> AwaitedTask | None:
    """The in-flight command that must hold off this topic's automatic snapshot,
    or None if the worktree is nobody else's to write.

    Tasks past their own timeout plus ``SNAPSHOT_HOLD_GRACE_S`` no longer hold:
    the registry is process-global and only cleared by a report, so a child that
    died with its container would otherwise hold forever."""
    now = time.time()
    live = [
        t
        for t in active_for_topic(topic_id)
        if now <= t.started_at + t.timeout_s + SNAPSHOT_HOLD_GRACE_S
    ]
    # The one that frees the hold last — that's when snapshots resume, so it is
    # the honest thing to name in a log line or on `cheese status`.
    return max(live, key=lambda t: t.started_at + t.timeout_s, default=None)


def checkpoint_worktree(
    project_id: uuid.UUID, topic_id: uuid.UUID, message: str | None = None
) -> str:
    """The automatic snapshot, with the hold applied. Returns what it did
    (``"held: <label>"`` / ``"snapshotted"``) for the caller's logs.

    Best-effort like every checkpoint path: a snapshot must never fail a turn."""
    held = snapshot_hold(topic_id)
    if held is not None:
        logger.info(
            "snapshot held for topic %s: background task %s still running",
            topic_id,
            held.label,
        )
        return f"held: {held.label}"
    try:
        if message is None:
            ws.snapshot_worktree(project_id, topic_id)
        else:
            ws.snapshot_worktree(project_id, topic_id, message)
    except Exception:  # noqa: BLE001 — git snapshot is best-effort
        logger.warning("snapshot failed for topic %s", topic_id, exc_info=True)
        return "failed"
    return "snapshotted"


def status_snapshot(topic_id: uuid.UUID) -> dict:
    """What `cheese status` shows about this topic's background commands — and,
    crucially, whether one of them is holding the automatic snapshot. A hold
    nobody can see is how "my edits aren't on the branch" becomes a mystery."""
    now = time.time()
    held = snapshot_hold(topic_id)
    return {
        "tasks": [
            {
                "label": t.label,
                "command": t.command,
                "running_s": round(now - t.started_at),
                "timeout_s": t.timeout_s,
                "log_path": t.log_path,
            }
            for t in active_for_topic(topic_id)
        ],
        "snapshot_held_by": held.label if held is not None else None,
    }


async def _catch_up_snapshot(task: AwaitedTask) -> None:
    """Take the snapshot that was held while ``task`` (and any sibling) ran.

    Called once the task is out of the registry, so it only lands when the LAST
    one finishes. Runs off the event loop — snapshotting shells out to jj, and
    this is reached from the reporting child's HTTP request."""
    if not ws.has_worktree(task.project_id, task.topic_id):
        return  # nothing was ever checked out for this topic — nothing to commit
    outcome = await asyncio.to_thread(
        checkpoint_worktree,
        task.project_id,
        task.topic_id,
        f"芝士 edits（后台任务「{task.label}」结束后的最终态）",
    )
    logger.info("catch-up snapshot after task %s: %s", task.id, outcome)


def summary(task: AwaitedTask, *, exit_code: int, tail: str, duration_s: float) -> str:
    """The result as it lands in the timeline and reaches the agent: everything
    needed to act without re-running anything — what ran, how it ended, how long
    it took, where the full output is, and the tail itself."""
    verdict = "成功" if exit_code == 0 else f"失败（退出码 {exit_code}）"
    if exit_code == 124:
        verdict = f"超时被终止（{task.timeout_s}s 上限）"
    clipped = tail[-TAIL_LIMIT:] if tail else "（没有输出）"
    return (
        f"⏱️ 后台任务「{task.label}」{verdict}，用时 {round(duration_s)}s。\n"
        f"命令：`{task.command}`\n"
        f"完整输出：{task.log_path}\n"
        f"输出尾部：\n```\n{clipped}\n```"
    )


def _wake_allowed(topic_id: uuid.UUID) -> bool:
    """Rolling-window storm brake. Records the wake when it allows one."""
    key = str(topic_id)
    now = time.time()
    window = _WAKES.setdefault(key, deque())
    while window and now - window[0] > WAKE_WINDOW_S:
        window.popleft()
    if len(window) >= MAX_WAKES_PER_WINDOW:
        return False
    window.append(now)
    return True


async def _settled_reason(session_factory, task: AwaitedTask) -> str | None:
    """Why this topic must not be woken at all, or None if it may be."""
    async with session_factory() as session:
        topic = await session.get(Topic, task.topic_id)
        if topic is None:
            return "话题不存在"
        if topic.status == TopicStatus.archived:
            return "话题已归档"
        settled = await session.scalars(
            select(AcceptCard.status)
            .where(AcceptCard.topic_id == task.topic_id)
            .where(AcceptCard.status.in_(_SETTLED_CARD_STATES))
            .limit(1)
        )
        if settled.first() is not None:
            return "验收卡已结算"
    return None


def _submit_wake(runner, chat_service, task: AwaitedTask, content: str) -> bool:
    """Wake the topic unless the storm brake says no. The brake is consulted HERE
    rather than at report time so a deferred wake can't slip past it either."""
    if not _wake_allowed(task.topic_id):
        logger.warning("await wake suppressed for topic %s: storm brake", task.topic_id)
        return False
    runner.submit(
        chat_service,
        task.topic_id,
        author="system",
        content=(
            f"你之前用 `cheese await` 丢到后台的任务跑完了。\n\n{content}\n\n"
            "接着处理它的结果：绿了就继续推进原来的活，红了就修。"
        ),
        summon=True,
        nudge_event=f"⏱️ 后台任务「{task.label}」跑完了，芝士来处理",
    )
    return True


def _defer_wake(
    session_factory, runner, chat_service, task: AwaitedTask, content: str
) -> None:
    """Wait for the topic's current turn to end, then wake it. Bounded by
    DEFER_CEILING_S — a wedged turn must not keep this pending forever. The
    settled/archived guard is re-checked on the way out: a topic can be accepted
    and archived during the wait, and that must still cancel the wake."""

    async def _later() -> None:
        deadline = time.time() + DEFER_CEILING_S
        while time.time() < deadline:
            await asyncio.sleep(DEFER_POLL_S)
            if task.topic_id in runner.running_topic_ids():
                continue
            blocked = await _settled_reason(session_factory, task)
            if blocked is not None:
                logger.info("await task %s dropped after defer: %s", task.id, blocked)
                return
            if _submit_wake(runner, chat_service, task, content):
                logger.info("await task %s woke topic after defer", task.id)
            return
        logger.info(
            "await task %s gave up deferring: topic %s still running after %ss",
            task.id,
            task.topic_id,
            DEFER_CEILING_S,
        )

    handle = asyncio.create_task(_later())
    _DEFERRED.add(handle)
    handle.add_done_callback(_DEFERRED.discard)


async def report(
    session_factory,
    chat_service,
    runner,
    *,
    task: AwaitedTask,
    exit_code: int,
    tail: str,
    duration_s: float,
) -> dict:
    """Land one finished background task and decide whether it wakes the topic.

    The block is landed FIRST and unconditionally: whatever the guards say, the
    result is in the timeline and a human can see it. Returns
    ``{"woke": bool, "reason": str}`` so the reporting child (and its tests) can
    tell what the platform did with it."""
    content = summary(task, exit_code=exit_code, tail=tail, duration_s=duration_s)
    forget(task.id)
    # The worktree is at rest again: take the snapshot this task was holding off,
    # BEFORE the result lands and (maybe) wakes a turn, so whoever reads the topic
    # next reads a settled branch. Never let it break the report — a dropped
    # report is the frozen topic this whole path exists to prevent.
    try:
        await _catch_up_snapshot(task)
    except Exception:  # noqa: BLE001
        logger.warning("catch-up snapshot failed for task %s", task.id, exc_info=True)
    await webhook_service.post_with_retries(
        session_factory,
        project_id=task.project_id,
        topic_id=task.topic_id,
        content=content,
        source="await",
    )

    blocked = await _settled_reason(session_factory, task)
    if blocked is not None:
        logger.info("await task %s landed without waking: %s", task.id, blocked)
        return {"woke": False, "reason": blocked}

    if task.topic_id in runner.running_topic_ids():
        # 话题正在跑：别插队，也别丢——等它空下来再叫。
        _defer_wake(session_factory, runner, chat_service, task, content)
        return {"woke": False, "reason": "话题正在跑，等它空闲后再唤醒"}

    if not _submit_wake(runner, chat_service, task, content):
        return {"woke": False, "reason": "已达自动唤醒上限（本条只记录，不唤醒）"}
    return {"woke": True, "reason": ""}
