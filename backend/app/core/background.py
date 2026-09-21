"""Fire-and-forget background work that actually survives.

``asyncio`` keeps only a WEAK reference to a running task. A task nobody else
holds can therefore be garbage-collected **mid-await** — the footgun the stdlib
documents in ``asyncio.create_task``: *"Save a reference to the result of this
function, to avoid a task disappearing mid-execution."*

Most of this codebase already does that (``AgentWorkRunner`` keeps
``self._tasks``). Three call sites did not, and each of
them exists to TELL A ROOM SOMETHING — the accept merged, the push finished, the
box was rebuilt. A dropped task there is not a crash: it is a topic that never
hears, which is the exact failure mode the platform is worst at surfacing.

``spawn`` is the one-liner replacement, so the fourth site can't reintroduce it:

    from app.core.background import spawn

    spawn(post_with_retries(...), name="accept notice")

Never awaited, never cancelled here — the caller has already decided this work
outlives it. A crash inside is logged rather than swallowed, because a bare
``create_task`` whose exception nobody retrieves only surfaces (if ever) as an
"exception was never retrieved" warning at GC time.

``hold`` is the same guarantee for work the caller keeps a handle on so it can
cancel it later, and ``PeriodicRunner`` is the idea for work that repeats: one
maintenance job, one interval, one place where every loop's boilerplate is
written down.

``periodic_jobs`` is that one place, and the two sweeps above it are the turn
liveness layer: the pair of jobs that notice work which died without taking the
process with it. They live here rather than in a scheduler component of their
own, because there is no scheduler — 结论 16: the 芝士 in a project's overview
room is the scheduler, and what remains around it is a clock. Domain imports are
function-local on purpose: this module is imported from everywhere, including
from the domains it starts.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from app.core.config import settings
from app.core.db import SessionFactory

if TYPE_CHECKING:
    from app.domain.agent.chat import ChatService

logger = logging.getLogger("cheesex.background")

# Strong references to everything in flight. Entries remove themselves on
# completion, so this is bounded by concurrency, not by history.
_INFLIGHT: set[asyncio.Task[Any]] = set()


def _report_failure(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        # ERROR, not WARNING: reaching here means work the caller handed off was
        # dropped — a room never told, a turn never resumed, spend never charged
        # — and `obs.AlertOnError` only picks up ERROR.
        logger.error("background task %r failed", task.get_name(), exc_info=exc)


def _finished(task: asyncio.Task[Any]) -> None:
    _INFLIGHT.discard(task)
    _report_failure(task)


def hold(
    task: asyncio.Task[Any], registry: set[asyncio.Task[Any]], *, name: str
) -> asyncio.Task[Any]:
    """Keep ``task`` in ``registry`` until it ends, and say so if it crashed.

    ``spawn`` is for work the caller will never touch again; this is for work the
    caller holds on to so it can cancel it at shutdown. Both need the same crash
    report, and hand-rolling half of it as ``add_done_callback(registry.discard)``
    is exactly how the other half gets lost: an exception nobody retrieves
    surfaces, if ever, as a warning at garbage-collection time attached to
    nothing, long after the work it was doing went missing.

    ``name`` is required because that report is the only thing anyone will have,
    and ``Task-4172 failed`` names nothing.

    A task that reports its own failures does not want this — an agent turn
    writes its own failure event, and a second line here would only double it.
    """
    task.set_name(name)
    registry.add(task)
    task.add_done_callback(registry.discard)
    task.add_done_callback(_report_failure)
    return task


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> bool:
    """Run ``coro`` in the background, holding a strong reference until it ends.

    Returns False (having closed the coroutine) when there is no running loop —
    sync tests and scripts call into these paths, and "nothing to schedule onto"
    is a no-op there, not an error. Closing it matters: an un-awaited coroutine
    that is merely dropped emits a "never awaited" RuntimeWarning.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return False
    task = loop.create_task(coro, name=name)
    _INFLIGHT.add(task)
    task.add_done_callback(_finished)
    return True


def inflight_count() -> int:
    """How many spawned tasks are still running — for tests and diagnostics."""
    return len(_INFLIGHT)


class PeriodicRunner:
    """One maintenance job on a clock, held so it cannot be collected.

    Every periodic job the platform runs wants the same four things, and gets
    them wrong in the same four ways when it hand-rolls them: a strong
    reference (see ``spawn`` above), an interval of ``0`` meaning "not on this
    box" rather than "as fast as possible", a crash that kills one cycle rather
    than the loop, and a log line only on the cycles that did something — a
    sweep that reports "swept 0" every minute is a sweep nobody reads.

    ``job`` returns whatever it likes; ``_worth_reporting`` decides whether the
    result is worth a line. A mapping speaks when any of its values does, so
    ``{"failed": 0, "errors": []}`` stays quiet and ``{"failed": 3}`` does not.
    """

    def __init__(
        self,
        name: str,
        interval_seconds: float,
        job: Callable[[], Awaitable[Any]],
    ) -> None:
        self._name = name
        self._interval = interval_seconds
        self._job = job
        self._task: asyncio.Task[None] | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def interval_seconds(self) -> float:
        """Seconds between runs; 0 or less means this box does not run it."""
        return self._interval

    def start(self) -> None:
        """Begin looping. A non-positive interval means this box does not run
        this job at all — the switch every deployment and every test uses."""
        if self._interval > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop(), name=self._name)
            logger.info("%s started (every %ss)", self._name, self._interval)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                result = await self._job()
            except asyncio.CancelledError:
                # `stop()` cancels this task, and THAT has to end the loop.
                # But a job can raise the same exception with nobody having
                # asked this loop to stop: a `wait_for` it ran, a task it
                # awaited, a session torn down under it. Letting that through
                # ends the job for the life of the process, with nothing in the
                # log to say so, which is the failure this class exists to
                # prevent, one level up from a job that was never scheduled.
                if _cancellation_requested():
                    raise
                logger.exception("%s was cancelled from inside; continuing", self._name)
                continue
            except Exception:  # noqa: BLE001 — one bad cycle must not end the loop
                logger.exception("%s failed", self._name)
                continue
            if _worth_reporting(result):
                logger.info("%s: %s", self._name, result)


def _cancellation_requested() -> bool:
    """Whether somebody asked the task running this loop to stop — `stop()`, or
    the event loop shutting down. `Task.cancelling()` counts those requests, and
    a `CancelledError` raised inside a job leaves the count at zero."""
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


def _worth_reporting(result: Any) -> bool:
    if isinstance(result, Mapping):
        return any(bool(value) for value in result.values())
    return bool(result)


# --- 轮次活性层 ---------------------------------------------------------------
#
# Both sweeps below answer the same question one layer apart: something the
# platform started is no longer running, and nobody would ever find out. A turn
# can die without taking the process with it (container recreate, OOM-killed
# child, sandbox image swap), and so can a gate check. The startup sweeps in
# `lifespan` only ever fire when the PROCESS restarts; these keep looking.


async def last_block_at(
    sessions: SessionFactory, topic_ids: set[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """Newest block timestamp per topic — the liveness probe the orphan sweep
    judges silence on. It is the same signal a human reads off the topic
    (「最后一块是几点」), which is what makes a sweep verdict checkable, and it is
    passed IN to `AgentWorkRunner.sweep_orphans` because the runner has no DB
    binding of its own."""
    if not topic_ids:
        return {}
    from sqlalchemy import func, select

    from app.domain.block.models import Block

    async with sessions() as session:
        rows = (
            await session.execute(
                select(Block.topic_id, func.max(Block.created_at))
                .where(Block.topic_id.in_(topic_ids))
                .group_by(Block.topic_id)
            )
        ).all()
    out: dict[uuid.UUID, datetime] = {}
    for topic_id, last in rows:
        if last is None:
            continue
        # The column is TIMESTAMPTZ but some drivers hand back a naive value,
        # and a naive one would blow up the subtraction rather than merely
        # being wrong.
        out[topic_id] = last if last.tzinfo is not None else last.replace(tzinfo=UTC)
    return out


async def sweep_orphan_turns(chat: "ChatService") -> int:
    """Periodic counterpart to the startup orphan sweep in `lifespan`.

    The startup one only ever runs when the PROCESS restarts, but a turn can die
    without taking the process with it. Nothing re-read the registry in that
    case, so the topic stayed `active` forever — see
    `AgentWorkRunner.sweep_orphans`, which holds the rules."""
    from app.api.deps import get_work_runner

    return await get_work_runner().sweep_orphans(
        chat,
        last_activity=lambda topic_ids: last_block_at(chat.session_factory, topic_ids),
        silence_s=settings.turn_silence_timeout_s,
    )


async def sweep_abandoned_gates(chat: "ChatService") -> dict:
    """闸门孤儿卡扫底 (2026-08-11): condemn `pending_gate` cards whose gate runner
    is gone, so their topic stops being unable to file a new card.

    `review/gate.py` dispatches its checks as in-memory `asyncio.create_task`s,
    so a redeploy kills every check in flight and nobody ever calls
    `finish_gate`. `pending_gate` has no other exit — accept / reject / revoke /
    reassign all refuse it, and `create_card`'s mutex refuses to file a new card
    — so what breaks is not one card but the topic's ability to ever hand over
    another one (2小时44分, measured live). The rules, and why a periodic sweep
    is needed on top of the startup one, live in `review/gate_sweep.py`."""
    from app.api.deps import get_work_runner
    from app.domain.agent.runtime import addressed_to_agent
    from app.domain.review import gate_sweep
    from app.domain.topic_membership.services import addressable_seat

    runner = get_work_runner()
    sessions = chat.session_factory

    async def nudge(topic_id: uuid.UUID, content: str, event: str, meta: dict) -> None:
        seat = await addressable_seat(sessions, topic_id)
        runner.submit(
            chat,
            topic_id,
            author="system",
            content=content,
            addressed=addressed_to_agent(seat),
            nudge_event=event,
            nudge_meta=meta,
        )

    return await gate_sweep.sweep(sessions, nudge=nudge)


# --- 平台跑着的每一个定时任务，一份清单 ----------------------------------------


class Sweeper(Protocol):
    """Anything with one sweep to run on a clock (the machine enroller)."""

    async def sweep(self) -> dict[str, int]: ...


def periodic_jobs(
    *,
    chat: "ChatService",
    machines: Sweeper,
    sessions: SessionFactory,
) -> list[PeriodicRunner]:
    """Every periodic job the platform runs, in one list.

    The failure this list exists to prevent is not a bug inside any one job. It
    is a job that ships with nothing to run it: the code is written, reviewed,
    imported and deployed, and then simply never executes. That failure is
    silent by construction — there is no error to read, only an absence — and
    the thing it takes away is exactly the thing nobody is watching for. A
    deadline passes and nobody is told. A queue fills and no mail leaves. The
    room looks normal.

    So the list is here rather than spread across the modules that each job
    belongs to, and `lifespan` starts what this returns. One place to read means
    one place to notice a job that is declared and named nowhere.

    Each entry's interval comes from settings, and 0 means "not on this box" —
    the only switch a job has. A job whose interval is 0 by DEFAULT is one no
    deployment runs unless it opts in; that is a decision, and it should be made
    on purpose.
    """
    from app.api.deps import get_work_runner
    from app.domain import backend_log
    from app.domain.agent.forgejo_tokens import purge_expired_tokens
    from app.domain.delivery.ledger import resend_unsent_deliveries
    from app.domain.delivery.timer import deliver_due
    from app.domain.machine.warm import sweep_warm_pool
    from app.domain.notification.maintenance import (
        drain_email_queue,
        finalize_expired_aggregations,
    )
    from app.domain.notification.push_delivery import drain_push_queue
    from app.domain.project.forge import reconcile_repository_webhooks
    from app.domain.review import pr_poll
    from app.domain.task.deadline_scheduler import sweep_expired_deadlines
    from app.domain.usage.subscription_ingest import ingest_once

    usage_log = settings.subscription_usage_log.strip()
    return [
        PeriodicRunner(
            "forge event subscriptions",
            300,
            lambda: reconcile_repository_webhooks(sessions),
        ),
        PeriodicRunner(
            "forge credential cache cleanup",
            3600,
            lambda: purge_expired_tokens(sessions),
        ),
        # 合并态轮询 (#718): mirrors pending PR cards' merge state — PR CI
        # → merge → deploy workflow → archive.
        PeriodicRunner(
            "pr poll",
            settings.accept_pr_poll_interval_s,
            lambda: pr_poll.poll_open_prs(chat),
        ),
        # 有东西就有 PR (#718 拍板①): a batch's draft PR opens at its first
        # commit, and the platform can only OBSERVE that commit (a 分身 commits
        # in the shared worktree — no push, no webhook, nothing to intercept).
        # Same clock as the poller above on purpose: this is the other half of
        # "watch the PRs", and a second interval setting would be one more knob
        # to get wrong.
        PeriodicRunner(
            "draft pr sweep",
            settings.accept_pr_poll_interval_s,
            lambda: pr_poll.open_draft_prs(chat),
        ),
        # The startup sweep in `lifespan` only fires when the PROCESS restarts; a
        # turn can be killed without that (container recreate, OOM, sandbox
        # swap) and then nothing would look again. This is what keeps looking.
        PeriodicRunner(
            "orphan sweep",
            settings.orphan_sweep_interval_s,
            lambda: sweep_orphan_turns(chat),
        ),
        PeriodicRunner(
            "chat progress reminder",
            settings.chat_progress_check_interval_s,
            chat.remind_silent_turns,
        ),
        # 闸门孤儿卡扫底: the same blind spot one layer down — a gate task can die
        # under a process that keeps running, and then the card waits forever
        # (see review/gate_sweep.py's module docstring).
        PeriodicRunner(
            "gate sweep",
            settings.gate_sweep_interval_s,
            lambda: sweep_abandoned_gates(chat),
        ),
        # Enrolling provisioned machines is platform plumbing, so it runs on its
        # own interval — see machine/runner.py.
        PeriodicRunner(
            "machine enrollment sweep",
            settings.machine_enroll_interval_seconds,
            machines.sweep,
        ),
        PeriodicRunner(
            "cloud warm pool",
            settings.machine_enroll_interval_seconds,
            lambda: sweep_warm_pool(sessions),
        ),
        # Subscription turns are metered at the proxy; this tails its log into
        # resource_usage + credits (issue #218). Off unless the log path is set.
        PeriodicRunner(
            "subscription usage ingest",
            settings.subscription_ingest_interval_s if usage_log else 0,
            lambda: ingest_once(sessions, Path(usage_log)),
        ),
        # 后端报错回房间 (issue #283): closes burst windows on a clock, so a flood
        # that stopped still reports its size instead of waiting for a
        # recurrence that a fixed bug never has.
        PeriodicRunner(
            "backend error flush",
            settings.backend_error_flush_interval_s,
            backend_log.flush_expired,
        ),
        # An aggregation window that never closes is a notification written and
        # never delivered — and aggregated notifications are what a busy room
        # produces most of.
        PeriodicRunner(
            "notification finalize",
            settings.notification_finalize_interval_s,
            lambda: finalize_expired_aggregations(sessions),
        ),
        # The only consumer of the Redis list every email notification is pushed
        # onto. Unrun, that key is not slow — it grows forever and no mail goes.
        PeriodicRunner(
            "notification email drain",
            settings.notification_email_drain_interval_s,
            lambda: drain_email_queue(sessions),
        ),
        # 同一个形状，同一个理由：那条 Redis 队列没有第二个消费者，不跑就是一条
        # 推送都发不出去。没配 VAPID 密钥时它自己空转（`web_push_configured`）。
        PeriodicRunner(
            "notification push drain",
            settings.notification_push_drain_interval_s,
            lambda: drain_push_queue(sessions),
        ),
        # 投递账本上那些「记下了、没发出去」的行的唯一出路。那一行已经和引发它的
        # 事件一起提交了，而发送这一半的进程可能在中间就没了 —— 不跑这个 job，账本
        # 就只是一份丢失记录，而不是一次补救。
        PeriodicRunner(
            "delivery resend",
            settings.delivery_resend_interval_s,
            lambda: resend_unsent_deliveries(sessions),
        ),
        # 定时投递（结论 17）：一个参与者设下的闹钟，到点由这里递出去。没有它，
        # `timed_deliveries` 就只是一张没人读的愿望清单 —— 而这份清单存在的理由，
        # 正是「写完了、部署了、从来没跑过」这种失败。
        PeriodicRunner(
            "timed deliveries",
            30,
            lambda: deliver_due(sessions, chat=chat, runner=get_work_runner()),
        ),
        # A deadline nobody sweeps is a promise the platform made and quietly did
        # not keep: the moment it matters is the moment nobody is looking.
        PeriodicRunner(
            "task deadline sweep",
            settings.task_deadline_sweep_interval_s,
            lambda: sweep_expired_deadlines(sessions),
        ),
    ]
