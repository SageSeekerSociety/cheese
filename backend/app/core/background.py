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

``PeriodicJobs`` keeps registrations local to one application composition. The
application lists the names in order; domains provide the intervals and work.
Nothing here knows which jobs the platform runs or imports the domains it serves.
"""

import asyncio
import contextlib
import logging
import os
import socket
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core import forge_http, forge_quota

logger = logging.getLogger("cheesex.background")

# Strong references to everything in flight. Entries remove themselves on
# completion, so this is bounded by concurrency, not by history.
_INFLIGHT: set[asyncio.Task[Any]] = set()

# Which process a tick was taken by, for the lease row's `run_by`: the hostname
# names the deployment slot (a container has one of its own), the pid the process
# inside it. Truncated to the column, which is longer than any hostname.
_PROCESS = f"{socket.gethostname()}:{os.getpid()}"[:200]


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


class RunRecord(Protocol):
    """Where a job's ticks are leased; `app.core.job_runs.JobRuns` in the app."""

    async def claim(
        self, name: str, *, interval_s: float, who: str, now: datetime
    ) -> bool: ...


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

    The clock survives the process. Each run's start is written to ``runs``, and
    a process that takes the jobs over starts each with the last run recorded
    (``last_run``), running it as soon as it is overdue. Counting from the
    process start instead, an hourly job on a box that redeploys every twenty
    minutes would never run. Without a recorded run a job is first due one
    interval after this start.

    ``owner_only`` says where the job is allowed to run, and so where
    ``main.lifespan`` starts it. True — every job today — means only the process
    holding the ownership lock (`core/ownership.py`) schedules it, and it starts
    when that lock is taken. False means the job no longer depends on the lock:
    every process starts it, and the tick lease it takes in ``runs`` is what
    keeps one tick from being run twice. A job moving out of the lock is flipped
    here, one job per PR.

    ``woken_by`` is for a job with work that should not wait for its clock:
    given the seconds to the next tick, it returns whether something woke the
    job before then, and the job runs at once. The clock still ticks, for a
    wake that was lost.
    """

    def __init__(
        self,
        name: str,
        interval_seconds: float,
        job: Callable[[], Awaitable[Any]],
        *,
        owner_only: bool = True,
        interval_policy: Callable[[float], float | None] | None = None,
        pause_wait: Callable[[], float] | None = None,
        woken_by: Callable[[float], Awaitable[bool]] | None = None,
    ) -> None:
        if woken_by is not None and not owner_only:
            raise ValueError(  # i18n-exempt: invalid scheduler setup for developers
                "a job woken between ticks must run on the owner only"
            )
        if interval_policy is not None and pause_wait is None:
            raise ValueError(  # i18n-exempt: invalid scheduler setup for developers
                "a paused interval policy needs a resume wait"
            )
        self._name = name
        self._interval = interval_seconds
        self._interval_policy = interval_policy or (lambda interval: interval)
        self._pause_wait = pause_wait
        self._job = job
        self._owner_only = owner_only
        self._woken_by = woken_by
        self._task: asyncio.Task[None] | None = None
        self._last_started_at: datetime | None = None
        self._last_succeeded_at: datetime | None = None
        self._running_since: datetime | None = None
        self._last_error: str | None = None

    def health(self) -> dict[str, Any]:
        if self._interval <= 0:
            status = "disabled"
        elif self._task is None:
            status = "not_started"
        elif self._task.done():
            status = "stopped"
        elif self._running_since is not None:
            status = "running"
        elif self._last_error is not None:
            status = "failed"
        else:
            status = "waiting"
        return {
            "status": status,
            "last_started_at": self._last_started_at,
            "last_succeeded_at": self._last_succeeded_at,
            "running_since": self._running_since,
            "last_error": self._last_error,
        }

    def _next_interval_seconds(self) -> float | None:
        try:
            interval = self._interval_policy(self._interval)
            return None if interval is None else max(0.0, interval)
        except (
            Exception
        ):  # i18n-exempt: internal fallback for an invalid interval policy
            logger.exception("%s: could not select its interval", self._name)
            return self._interval

    def _pause_wait_seconds(self) -> float:
        if self._pause_wait is None:
            return self._interval
        try:
            return max(0.1, self._pause_wait())
        except Exception:  # retry policy on the next base interval
            logger.exception("%s: could not select its resume wait", self._name)
            return self._interval

    @property
    def name(self) -> str:
        return self._name

    @property
    def interval_seconds(self) -> float:
        """Seconds between runs; 0 or less means this box does not run it."""
        return self._interval

    @property
    def owner_only(self) -> bool:
        """Whether only the process holding the ownership lock runs this."""
        return self._owner_only

    def start(self, runs: "RunRecord", last_run: datetime | None = None) -> None:
        """Begin looping. A non-positive interval means this box does not run
        this job at all — the switch every deployment and every test uses."""
        if self._interval > 0 and self._task is None:
            interval = self._next_interval_seconds()
            wait = self._pause_wait_seconds() if interval is None else interval
            if interval is not None and last_run is not None:
                since = (datetime.now(UTC) - last_run).total_seconds()
                wait = max(0.0, interval - since)
            self._task = asyncio.create_task(
                self._loop(runs, wait, interval), name=self._name
            )
            if interval is None:
                logger.info("%s paused until quota reset", self._name)
            else:
                logger.info("%s started (every %ss)", self._name, interval)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _take_tick(self, runs: "RunRecord") -> bool:
        """Whether this tick is this process's to run.

        The lease is one row per job, taken for one interval: whichever process
        wins the UPDATE runs this tick, and the others move on. That is what
        lets a job be scheduled on every process — the ownership lock no longer
        being between it and its schedule (``owner_only``) — without the tick
        running twice or being recorded twice.

        Losing the race is the ordinary case for such a job, not a failure:
        nothing is logged, nothing is reported, the next tick comes.

        A lease that cannot be TAKEN is the opposite case. It is how two ticks
        are kept apart, and refusing to run on a database blip would stop every
        scheduled job on the box at once — including the ones that would have
        noticed a job that stopped. So the tick runs anyway, at ERROR.
        """
        try:
            return await runs.claim(
                self._name,
                # Throttling changes the wait, not the lease: a recovered quota
                # must not remain blocked by the previous slower interval.
                interval_s=self._interval,
                who=_PROCESS,
                now=datetime.now(UTC),
            )
        except Exception:  # a tick that cannot be leased still runs
            logger.exception("%s: could not take its tick; running anyway", self._name)
            return True

    async def _run_once(self) -> None:
        """One run of the job, its outcome recorded and never raised."""
        try:
            self._last_started_at = self._running_since = datetime.now(UTC)
            try:
                # Tag only the job's own forge requests. The context is
                # reset by the context manager on every exit path, so a
                # failed or cancelled cycle cannot leak "job" into later
                # work; scheduling and pause behaviour are untouched.
                with forge_http.background_job(self._name):
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
                self._last_error = "cancelled inside the job"
                logger.exception("%s was cancelled from inside; continuing", self._name)
            except Exception as exc:  # one bad cycle must not end the loop
                self._last_error = type(exc).__name__
                logger.exception("%s failed", self._name)
            else:
                self._last_succeeded_at = datetime.now(UTC)
                self._last_error = None
                if _worth_reporting(result):
                    logger.info("%s: %s", self._name, result)
        finally:
            self._running_since = None

    async def _loop(
        self, runs: "RunRecord", wait: float, interval: float | None
    ) -> None:
        loop = asyncio.get_running_loop()
        cycle_started = loop.time()
        if interval is not None:
            cycle_started -= interval - wait
            due_at = cycle_started + interval
        else:
            due_at = cycle_started
        while True:
            if interval is None:
                await asyncio.sleep(wait)
                interval = self._next_interval_seconds()
                if interval is None:
                    wait = self._pause_wait_seconds()
                    continue
                due_at = loop.time()
            else:
                delay = max(0.0, due_at - loop.time())
                if self._woken_by is None:
                    await asyncio.sleep(delay)
                elif await self._woken_by(delay):
                    # Woken between ticks. Only the owner runs a job that can be
                    # woken, one run at a time in this loop, so no lease is
                    # taken, and the clock's next tick stays where it was.
                    await self._run_once()
                    continue
                interval = self._next_interval_seconds()
                if interval is None:
                    wait = self._pause_wait_seconds()
                    continue
                due_at = cycle_started + interval
                if due_at > loop.time():
                    continue
            assert interval is not None
            if not await self._take_tick(runs):
                cycle_started = loop.time()
                interval = self._next_interval_seconds()
                if interval is None:
                    wait = self._pause_wait_seconds()
                else:
                    due_at = cycle_started + interval
                continue
            await self._run_once()
            cycle_started = loop.time()
            interval = self._next_interval_seconds()
            if interval is None:
                wait = self._pause_wait_seconds()
            else:
                due_at = cycle_started + interval


def periodic_health(
    jobs: Sequence[PeriodicRunner], *, owns_work: bool
) -> dict[str, Any]:
    readings = {
        job.name: job.health() for job in jobs if owns_work or not job.owner_only
    }
    unhealthy = {
        name: reading["status"]
        for name, reading in readings.items()
        if reading["status"] in {"not_started", "stopped", "failed"}
    }
    report: dict[str, Any] = {
        "status": "down" if unhealthy else "up",
        "owns_work": owns_work,
        "unhealthy": unhealthy,
        "jobs": readings,
    }
    if unhealthy:
        # The one line every reader of the report shows (the admin overview
        # keeps only status and `error`): which clock, in what state, and why.
        report["error"] = "; ".join(
            f"{name}: {status}"
            + (
                f" ({readings[name]['last_error']})"
                if readings[name].get("last_error")
                else ""
            )
            for name, status in unhealthy.items()
        )
    return report


async def watch_periodic_jobs(
    read: Callable[[], dict[str, Any]], *, interval_s: float = 10, grace_s: float = 60
) -> None:
    """Notice clocks that never started or stopped, independently of those clocks."""
    unseen: dict[str, float] = {}
    reported: set[str] = set()
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(interval_s)
        unhealthy = read()["unhealthy"]
        unseen = {name: at for name, at in unseen.items() if name in unhealthy}
        reported.intersection_update(unhealthy)
        for name, status in unhealthy.items():
            since = unseen.setdefault(name, loop.time())
            # Startup discovery gets a minute; it must not hide a clock for
            # the lifetime of the process. HTTP readiness remains independent.
            if loop.time() - since >= grace_s and name not in reported:
                logger.error(
                    "periodic job %s has stayed %s for %ss", name, status, grace_s
                )
                reported.add(name)


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


class PeriodicJobs:
    """Instance-owned registrations, checked against the application's manifest.

    Registration does not run work. An application must explicitly register all
    jobs and request its complete ordered manifest before starting any of them:
    a duplicate, missing or unlisted job is a boot error, never silent absence.
    No domain context or process-global registration state lives here.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, PeriodicRunner] = {}

    def register(
        self,
        name: str,
        interval_seconds: float,
        job: Callable[[], Awaitable[Any]],
        *,
        owner_only: bool = True,
        forge_bound: bool = False,
        woken_by: Callable[[float], Awaitable[bool]] | None = None,
    ) -> None:
        if name in self._jobs:
            # i18n-exempt: startup wiring error for developers, not a user refusal
            raise ValueError(f"periodic job registered twice: {name!r}")
        self._jobs[name] = PeriodicRunner(
            name,
            interval_seconds,
            job,
            owner_only=owner_only,
            interval_policy=(
                forge_quota.background_interval_seconds if forge_bound else None
            ),
            pause_wait=forge_quota.background_resume_after if forge_bound else None,
            woken_by=woken_by,
        )

    def ordered(self, names: Sequence[str]) -> list[PeriodicRunner]:
        if len(names) != len(set(names)):
            # i18n-exempt: startup wiring error for developers, not a user refusal
            raise ValueError("periodic job manifest contains duplicate names")
        missing = set(names) - self._jobs.keys()
        unlisted = self._jobs.keys() - set(names)
        if missing or unlisted:
            # i18n-exempt: startup wiring error for developers, not a user refusal
            raise ValueError(
                f"periodic job manifest mismatch: missing={sorted(missing)!r}, "
                f"unlisted={sorted(unlisted)!r}"
            )
        return [self._jobs[name] for name in names]
