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
# in-flight tasks, which costs a wake (the child's report 404s and it gives up)
# but never corrupts anything. Persisting them would not help — the child that
# was going to report died with the container in that scenario anyway.
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
