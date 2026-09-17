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
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from typing import Any

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
