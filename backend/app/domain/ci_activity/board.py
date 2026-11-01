"""The numbers the CI activity page draws, computed from the stored tables.

Every figure says what it was computed over (``n``) and is ``None`` when there
was nothing to compute it from: a day with no merge has no median, and a median
drawn as 0 reads as a queue that took no time at all.

**Definitions**, each the one `scripts/ci/` and the hand measurements use, so the
page and a script over the same runs agree:

* **Runner wait** — a job's ``started_at - created_at``, over jobs that got a
  runner. GitHub creates a job when its ``needs`` are met, so this is time spent
  queued for a machine, not time spent waiting for an upstream job.
* **`CI required` duration** — a full-tier `Required CI` run (one whose first
  attempt has a job named exactly ``CI required``; a draft's is ``CI required
  (draft quick)``), concluded success or failure: the run's ``created_at`` to
  the last completion among its first attempt's jobs. First attempt only, so a
  re-run's wait is not counted as CI time (`docs/ci-feedback.md` measures to the
  latest completion instead and says so).
* **Merge-queue time** — a PR merged through the queue in the window: its first
  ``added`` to its ``merged`` removal. Every ejection and re-entry in between is
  part of the time it took.
* **CI rounds** — for each PR merged in the window, directly or through the queue: the
  `Required CI` runs on its ``pull_request`` events that reached a verdict
  (success or failure; a run cancelled by a newer push is not a round), and
  separately its ``merge_group`` runs that reached one.
* **Fail-then-pass** — a `Required CI` run whose latest attempt succeeded while
  an earlier attempt of the same run, on the same head, had a failed job: the
  failure did not reproduce. Per job name, a job that failed on one attempt and
  succeeded on a later one of the same run.
* **Busy runners** — jobs that had a runner and both stamps, counted at each
  minute: a job running at 10:00:00 counts in the 10:00 sample.

Windows are whole UTC days (`day_windows.utc_day_window`), the convention every
other block on the admin dashboard uses; hours of the day are UTC too.
"""

from __future__ import annotations

import asyncio
import bisect
import statistics
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, NamedTuple

from cachetools import TTLCache
from sqlalchemy import Row, Select, and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domain.ci_activity.models import (
    CiActivitySweep,
    CiJob,
    CiJobStep,
    CiMergedPullRequest,
    CiMergeQueueEvent,
    CiWorkflowRun,
)
from app.domain.day_windows import utc_day_window, utc_now

REQUIRED_CI_PATH = ".github/workflows/required-ci.yml"
#: The aggregating job of the full tier; a draft's is `CI required (draft quick)`.
FULL_TIER_JOB = "CI required"
MERGED = "merged"
VERDICTS = ("success", "failure")

#: What a run was for, as the page groups busy runners. `pr` is a pull request's
#: own CI, `queue` the merge queue's, `main` anything pushed to a branch (main's
#: builds, deploys, metrics), `other` the rest (schedules, dispatches).
_EVENT_CLASS = {
    "pull_request": "pr",
    "pull_request_target": "pr",
    "merge_group": "queue",
    "push": "main",
    "workflow_run": "main",
}
CLASSES = ("pr", "queue", "main", "other")
#: Rows a per-job or per-step table returns, at most.
TABLE_LIMIT = 60


def event_class(event: str) -> str:
    return _EVENT_CLASS.get(event, "other")


def percentile(values: list[float], p: float) -> float | None:
    """Linear interpolation between the two nearest ranks (`ci-duration-report.py`'s
    `pctl`), or None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summary(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "mean": statistics.fmean(values) if values else None,
        "p50": percentile(values, 0.5),
        "p90": percentile(values, 0.9),
        "max": max(values) if values else None,
    }


def _seconds(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return (b - a).total_seconds()


def _day(moment: datetime) -> date:
    return moment.astimezone(UTC).date()


def runner_group(kind: str | None, name: str | None, labels: list[str]) -> str:
    """The machine a job ran on, as the page lists them: a self-hosted runner by
    its name; GitHub's runners by the labels asked for, since each hosted job
    gets a runner of its own name."""
    if kind == "self_hosted":
        return name or "self-hosted"
    return "hosted: " + (",".join(labels) or "?")


@dataclass(frozen=True)
class Window:
    since: datetime
    until: datetime
    days: list[date]


def window(days: int, now: datetime | None = None) -> Window:
    """The last ``days`` whole UTC days up to the day ``now`` falls on."""
    since, until, buckets = utc_day_window(days, now)
    return Window(since, until, buckets)


#: Rows read per round trip. Each batch becomes rows on the event loop, so a
#: batch is kept small enough not to hold the loop up for long.
_BATCH = 500


async def _rows(
    session: AsyncSession, statement: Select, *, batch: int = _BATCH
) -> list[Row]:
    """Every row of ``statement``, read in batches so no one batch holds the
    event loop for long. Plain rows, tied to no session: the figures are worked
    out from them in a thread."""
    result = await session.stream(statement.execution_options(yield_per=batch))
    out: list[Row] = []
    async for part in result.partitions():
        out.extend(part)
    return out


class _Job(NamedTuple):
    name: str
    created_at: datetime | None
    started_at: datetime
    completed_at: datetime | None
    conclusion: str | None
    runner_kind: str
    runner: str
    event: str
    workflow_name: str
    workflow_path: str


#: The machine a job ran on (`runner_group`), worked out by the database.
_RUNNER = case(
    (
        CiJob.runner_kind == "self_hosted",
        func.coalesce(CiJob.runner_name, "self-hosted"),
    ),
    else_="hosted: "
    + func.coalesce(func.nullif(func.array_to_string(CiJob.labels, ","), ""), "?"),
)


async def _columns(session: AsyncSession, statement: Select, by: str) -> list[list]:
    """``statement``'s rows as one list per column, read an hour (of the
    ``by`` column) at a time.

    Columns rather than a row each: a month is ~90k jobs, and holding that many
    row objects at once sets off full garbage collections that stop the event
    loop for a tenth of a second each. A list of datetimes or strings is one
    object to the collector, however long."""
    rows = statement.subquery()
    hours = await _rows(
        session,
        select(*(func.array_agg(c) for c in rows.c)).group_by(
            func.date_trunc("hour", rows.c[by])
        ),
        # An hour is at most a few hundred rows, each a value in every column.
        batch=6,
    )
    out: list[list] = [[] for _ in rows.c]
    for hour in hours:
        for into, values in zip(out, hour, strict=True):
            into.extend(values)
    return out


async def _jobs_in(session: AsyncSession, repo: str, w: Window) -> list[list]:
    """Jobs that started in the window and had a runner, with their run's
    event: one list per `_Job` field."""
    return await _columns(
        session,
        select(
            CiJob.name,
            CiJob.created_at,
            CiJob.started_at,
            CiJob.completed_at,
            CiJob.conclusion,
            CiJob.runner_kind,
            _RUNNER.label("runner"),
            CiWorkflowRun.event,
            CiWorkflowRun.workflow_name,
            CiWorkflowRun.workflow_path,
        )
        .join(CiWorkflowRun, CiWorkflowRun.id == CiJob.run_row_id)
        .where(
            CiJob.repo == repo,
            CiJob.runner_kind.is_not(None),
            CiJob.started_at >= w.since,
            CiJob.started_at < w.until,
        ),
        "started_at",
    )


def _each[T](columns: list[list], row: Callable[..., T]) -> Iterator[T]:
    return (row(*values) for values in zip(*columns, strict=True))


_FAILED_JOB = aliased(CiJob)


def _required_in(repo: str, since: datetime, until) -> list:
    return [
        CiWorkflowRun.repo == repo,
        CiWorkflowRun.workflow_path == REQUIRED_CI_PATH,
        CiWorkflowRun.created_at >= since,
        CiWorkflowRun.created_at < until,
    ]


class _Run(NamedTuple):
    event: str
    conclusion: str | None
    created_at: datetime
    run_attempt: int
    full_tier: bool
    last_done: datetime | None
    had_a_failed_job: bool


async def _required_runs(
    session: AsyncSession, repo: str, since: datetime, until
) -> list[list]:
    """`Required CI` runs created in the window, each with whether its first
    attempt is the full tier (has a job named exactly `FULL_TIER_JOB`), the
    last completion among that attempt's jobs, and whether any attempt had a
    failed job, worked out by the database: one list per `_Run` field."""
    return await _columns(
        session,
        select(
            CiWorkflowRun.event,
            CiWorkflowRun.conclusion,
            CiWorkflowRun.created_at,
            CiWorkflowRun.run_attempt,
            func.coalesce(func.bool_or(CiJob.name == FULL_TIER_JOB), False).label(
                "full_tier"
            ),
            func.max(CiJob.completed_at).label("last_done"),
            exists()
            .where(
                _FAILED_JOB.run_row_id == CiWorkflowRun.id,
                _FAILED_JOB.conclusion == "failure",
            )
            .label("had_a_failed_job"),
        )
        .outerjoin(
            CiJob, and_(CiJob.run_row_id == CiWorkflowRun.id, CiJob.run_attempt == 1)
        )
        .where(*_required_in(repo, since, until))
        .group_by(CiWorkflowRun.id),
        "created_at",
    )


async def _queue_rows(
    session: AsyncSession, repo: str, w: Window
) -> tuple[dict[int, datetime], list[Row]]:
    """PRs merged through the queue in the window, and their ``added`` events."""
    merged = aliased(CiMergeQueueEvent)
    merged_in_window = select(merged.pr_number, merged.at).where(
        merged.repo == repo,
        merged.kind == "removed",
        merged.reason == MERGED,
        merged.at >= w.since,
        merged.at < w.until,
    )
    merges = {pr: at for pr, at in await _rows(session, merged_in_window)}
    if not merges:
        return merges, []
    adds = await _rows(
        session,
        select(CiMergeQueueEvent.pr_number, CiMergeQueueEvent.at).where(
            CiMergeQueueEvent.repo == repo,
            CiMergeQueueEvent.kind == "added",
            CiMergeQueueEvent.pr_number.in_(
                merged_in_window.with_only_columns(merged.pr_number)
            ),
        ),
    )
    return merges, adds


def _queue_passages(merges: dict[int, datetime], adds: list[Row]):
    """Each PR merged through the queue in the window: (pr, first added, merged)."""
    first: dict[int, datetime] = {}
    count: Counter = Counter()
    for pr, at in adds:
        if at <= merges[pr]:
            count[pr] += 1
            if pr not in first or at < first[pr]:
                first[pr] = at
    return [
        (pr, first[pr], merges[pr], count[pr]) for pr in sorted(merges) if pr in first
    ]


def _minute_samples(starts: list[datetime], ends: list[datetime], w: Window, until):
    """How many spans (``starts[i]`` to ``ends[i]``) cover each minute from the
    window's start to ``until``."""
    starts = sorted(starts)
    ends = sorted(ends)
    samples = []
    moment = w.since
    while moment < until:
        samples.append(
            bisect.bisect_right(starts, moment) - bisect.bisect_right(ends, moment)
        )
        moment += timedelta(minutes=1)
    return samples


#: How long a worker keeps a board it built. On dev (2026-10-31, ~41k jobs a
#: week, ~168k a month) a 7-day board takes about a second to build and a 30-day
#: one about six: worked out off the event loop (`build_board`), but still
#: database time and a thread, so it is built at most once a minute per window.
#: The board says when it was built (``generated_at``), and the page shows it.
BOARD_TTL_S = 60
_boards: TTLCache = TTLCache(maxsize=32, ttl=BOARD_TTL_S)
#: One lock per repository and window, held while that board is built, so
#: builds of one board never overlap: a burst of requests after it ages out
#: waits for one build, and if that build fails or is cancelled the next waiter
#: builds in its place, alone. Never removed: there are at most 30 windows.
_building: dict[tuple[str, int], asyncio.Lock] = {}


async def kept_board(
    repo: str, days: int, build: Callable[[], Awaitable[dict[str, Any]]]
) -> dict[str, Any]:
    """The board for ``days``, as built by ``build`` at most ``BOARD_TTL_S`` ago.
    Requests that find no board wait for the one build already running, each
    with its own session should that build not finish."""
    # The window is whole UTC days, so a board built before midnight is not
    # the board after it, however recently it was built.
    key = (repo, days, utc_now().date())
    board: dict[str, Any] | None = _boards.get(key)
    if board is not None:
        return board
    async with _building.setdefault((repo, days), asyncio.Lock()):
        board = _boards.get(key)
        if board is None:
            board = _boards[key] = await build()
    return board
    lock = _building.setdefault(key, asyncio.Lock())
    async with lock:
        board = _boards.get(key)
        if board is None:
            try:
                board = _boards[key] = await build()
            finally:
                # Whoever waits on this lock finds the board, or builds it
                # with its own session if this build failed.
                if _building.get(key) is lock:
                    del _building[key]
    return board


@dataclass(frozen=True)
class _BoardRows:
    """Everything the board is worked out from, read before any of it is."""

    jobs: list[list]
    merges: dict[int, datetime]
    adds: list[Row]
    queue_events: list[Row]
    removals: list[Row]
    runs: list[list]
    rounds: tuple


async def build_board(
    session: AsyncSession, repo: str, *, days: int, now: datetime | None = None
) -> dict[str, Any]:
    """The board: the rows read here on the event loop, a batch at a time, and
    the figures worked out from them in a thread, so neither holds the loop and
    every other request on this worker waiting."""
    now = now or utc_now()
    w = window(days, now)
    sampled_until = min(now, w.until)
    merges, adds = await _queue_rows(session, repo, w)
    runs = await _required_runs(session, repo, w.since, w.until)
    rows = _BoardRows(
        jobs=await _jobs_in(session, repo, w),
        merges=merges,
        adds=adds,
        queue_events=await _queue_event_rows(session, repo, sampled_until),
        removals=await _removal_rows(session, repo, w),
        runs=runs,
        rounds=await _rounds_rows(session, repo, w),
    )
    board = await asyncio.to_thread(_board, rows, repo, days, w, now, sampled_until)
    board["sweep"] = await _sweep_status(session, repo, w)
    return board


def _board(
    rows: _BoardRows,
    repo: str,
    days: int,
    w: Window,
    now: datetime,
    sampled_until: datetime,
) -> dict[str, Any]:
    # --- runners: wait, busy time, per machine, concurrency -------------------
    waits: dict[str, list[float]] = defaultdict(list)
    wait_by_day: dict[tuple[str, date], list[float]] = defaultdict(list)
    machines: dict[str, dict[str, Any]] = {}
    # Each span's start and end kept apart, not as pairs: no tuple per job.
    starts: dict[str, list[datetime]] = defaultdict(list)
    ends: dict[str, list[datetime]] = defaultdict(list)
    busy_seconds: dict[str, float] = defaultdict(float)
    for job in _each(rows.jobs, _Job):
        wait = _seconds(job.created_at, job.started_at)
        took = _seconds(job.started_at, job.completed_at)
        kind = job.runner_kind
        group = job.runner
        machine = machines.setdefault(
            group,
            {
                "runner": group,
                "kind": kind,
                "jobs": 0,
                "failed": 0,
                "busy_s": 0.0,
                "waits": [],
                "durations": [],
            },
        )
        machine["jobs"] += 1
        machine["failed"] += int(job.conclusion == "failure")
        if wait is not None and wait >= 0:
            waits["all"].append(wait)
            waits[kind].append(wait)
            machine["waits"].append(wait)
            wait_by_day[(kind, _day(job.started_at))].append(wait)
        if took is not None and took > 0:
            machine["busy_s"] += took
            machine["durations"].append(took)
            cls = event_class(job.event)
            busy_seconds[cls] += took
            end = job.completed_at
            assert end is not None  # a duration needs both stamps
            for key in (cls, kind):
                starts[key].append(job.started_at)
                ends[key].append(end)

    per_class = {
        cls: _minute_samples(starts[cls], ends[cls], w, sampled_until)
        for cls in CLASSES
    }
    per_kind = {
        k: _minute_samples(starts[k], ends[k], w, sampled_until)
        for k in ("hosted", "self_hosted")
    }
    total = [sum(v[i] for v in per_class.values()) for i in range(len(per_class["pr"]))]

    # --- the merge queue -----------------------------------------------------
    passages = _queue_passages(rows.merges, rows.adds)
    queue_minutes = [(m - a).total_seconds() / 60 for _, a, m, _ in passages]
    queue_by_day: dict[date, list[float]] = defaultdict(list)
    for _, added, merged_at, _ in passages:
        queue_by_day[_day(merged_at)].append((merged_at - added).total_seconds() / 60)
    depth = _queue_depth(rows.queue_events, w, sampled_until)
    ejections = _ejections(rows.removals)

    # --- Required CI: duration, rounds, fail-then-pass -------------------------
    durations: dict[str, list[float]] = defaultdict(list)
    duration_by_day: dict[tuple[str, date], list[float]] = defaultdict(list)
    for run in _each(rows.runs, _Run):
        if run.conclusion not in VERDICTS or not run.full_tier:
            continue
        took = _seconds(run.created_at, run.last_done)
        if took is None or took <= 0:
            continue
        durations[run.event].append(took / 60)
        duration_by_day[(run.event, _day(run.created_at))].append(took / 60)

    rounds = _rounds_from(*rows.rounds)
    retried = [r for r in _each(rows.runs, _Run) if r.run_attempt > 1]
    verdict_runs = sum(r.conclusion in VERDICTS for r in _each(rows.runs, _Run))
    fail_then_pass = [
        r for r in retried if r.conclusion == "success" and r.had_a_failed_job
    ]

    hourly = _hourly(w, sampled_until, total, per_class, per_kind, depth)
    return {
        "repo": repo,
        "days": days,
        "since": w.since.isoformat(),
        "until": w.until.isoformat(),
        "generated_at": now.isoformat(),
        "headline": {
            "merge_queue_minutes": summary(queue_minutes),
            "runner_wait_seconds": {
                k: summary(waits[k]) for k in ("all", "hosted", "self_hosted")
            },
            "ci_required_minutes": {
                e: summary(durations[e]) for e in ("pull_request", "merge_group")
            },
            "rounds": rounds,
            "fail_then_pass": {
                "runs": len(fail_then_pass),
                "of": verdict_runs,
                "share": (len(fail_then_pass) / verdict_runs) if verdict_runs else None,
            },
            "busy_runners": {
                **summary([float(x) for x in total]),
                "share": _shares(busy_seconds),
            },
        },
        "daily": [
            {
                "day": day.isoformat(),
                "merged": len(queue_by_day[day]),
                "merge_queue_p50": percentile(queue_by_day[day], 0.5),
                "merge_queue_p90": percentile(queue_by_day[day], 0.9),
                "runner_wait_hosted_p50": percentile(wait_by_day[("hosted", day)], 0.5),
                "runner_wait_self_hosted_p50": percentile(
                    wait_by_day[("self_hosted", day)], 0.5
                ),
                "ci_required_pr_p50": percentile(
                    duration_by_day[("pull_request", day)], 0.5
                ),
                "ci_required_queue_p50": percentile(
                    duration_by_day[("merge_group", day)], 0.5
                ),
            }
            for day in w.days
        ],
        "hourly": hourly,
        "hour_of_day": _hour_of_day(hourly),
        "runners": sorted(
            (
                {
                    "runner": m["runner"],
                    "kind": m["kind"],
                    "jobs": m["jobs"],
                    "failed": m["failed"],
                    "busy_hours": m["busy_s"] / 3600,
                    "utilisation": (
                        m["busy_s"] / (sampled_until - w.since).total_seconds()
                        if m["kind"] == "self_hosted"
                        else None
                    ),
                    "wait_p50_s": percentile(m["waits"], 0.5),
                    "duration_p50_s": percentile(m["durations"], 0.5),
                }
                for m in machines.values()
            ),
            key=lambda m: -m["busy_hours"],
        ),
        "ejections": ejections,
    }


def _shares(seconds: dict[str, float]) -> dict[str, float | None]:
    total = sum(seconds.values())
    return {cls: (seconds[cls] / total if total else None) for cls in CLASSES}


def _hourly(w, until, total, per_class, per_kind, depth) -> list[dict[str, Any]]:
    out = []
    for start in range(0, len(total), 60):
        hour = w.since + timedelta(minutes=start)
        part = slice(start, start + 60)
        out.append(
            {
                "hour": hour.isoformat(),
                "busy_mean": statistics.fmean(total[part]),
                "busy_peak": max(total[part]),
                **{f"{c}_mean": statistics.fmean(per_class[c][part]) for c in CLASSES},
                "hosted_mean": statistics.fmean(per_kind["hosted"][part]),
                "self_hosted_mean": statistics.fmean(per_kind["self_hosted"][part]),
                "queue_depth_peak": max(depth[part]) if depth[part] else 0,
            }
        )
    return out


def _hour_of_day(hourly: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_hour: dict[int, list[dict]] = defaultdict(list)
    for row in hourly:
        by_hour[datetime.fromisoformat(row["hour"]).hour].append(row)
    return [
        {
            "hour": hour,
            "busy_mean": statistics.fmean(r["busy_mean"] for r in rows)
            if rows
            else None,
            "busy_peak": max((r["busy_peak"] for r in rows), default=None),
            **{
                f"{c}_mean": statistics.fmean(r[f"{c}_mean"] for r in rows)
                if rows
                else None
                for c in CLASSES
            },
        }
        for hour, rows in ((h, by_hour.get(h, [])) for h in range(24))
    ]


async def _queue_event_rows(session, repo, until) -> list[Row]:
    return await _rows(
        session,
        select(
            CiMergeQueueEvent.pr_number, CiMergeQueueEvent.kind, CiMergeQueueEvent.at
        )
        .where(CiMergeQueueEvent.repo == repo, CiMergeQueueEvent.at < until)
        .order_by(CiMergeQueueEvent.at),
    )


def _queue_depth(rows: list[Row], w: Window, until) -> list[int]:
    """PRs in the merge queue at each minute of the window."""
    entered: dict[int, datetime] = {}
    starts: list[datetime] = []
    ends: list[datetime] = []
    for pr, kind, at in rows:
        if kind == "added":
            entered.setdefault(pr, at)
        elif pr in entered:
            starts.append(entered.pop(pr))
            ends.append(at)
    starts.extend(entered.values())
    ends.extend(until for _ in entered)
    return _minute_samples(starts, ends, w, until)


async def _removal_rows(session, repo, w: Window) -> list[Row]:
    return await _rows(
        session,
        select(CiMergeQueueEvent.reason, CiMergeQueueEvent.actor).where(
            CiMergeQueueEvent.repo == repo,
            CiMergeQueueEvent.kind == "removed",
            CiMergeQueueEvent.at >= w.since,
            CiMergeQueueEvent.at < w.until,
        ),
    )


def _ejections(rows: list[Row]) -> list[dict[str, Any]]:
    counts: Counter = Counter()
    for reason, actor in rows:
        if reason == MERGED:
            continue
        counts[(reason or "?", actor or "?")] += 1
    return [
        {"reason": reason, "actor": actor, "count": n}
        for (reason, actor), n in counts.most_common()
    ]


async def _rounds(session, repo, w: Window) -> dict[str, Any]:
    """Required CI rounds of each PR merged in the window (`_rounds_from`)."""
    return _rounds_from(*await _rounds_rows(session, repo, w))


async def _rounds_rows(
    session, repo, w: Window
) -> tuple[list[Row], datetime | None, list[list]]:
    """The PRs merged in the window, how far back the run listing reaches, and
    the `Required CI` runs that could be theirs."""
    pulls = await _rows(
        session,
        select(
            CiMergedPullRequest.pr_number,
            CiMergedPullRequest.head_branch,
            CiMergedPullRequest.created_at,
            CiMergedPullRequest.merged_at,
        ).where(
            CiMergedPullRequest.repo == repo,
            CiMergedPullRequest.merged_at >= w.since,
            CiMergedPullRequest.merged_at < w.until,
        ),
    )
    covered_from = await session.scalar(
        select(CiActivitySweep.runs_listed_from)
        .where(CiActivitySweep.repo == repo)
        .order_by(CiActivitySweep.started_at.desc())
        .limit(1)
    )
    if not pulls:
        return pulls, covered_from, [[], [], [], []]
    merged_in_window = (
        CiMergedPullRequest.repo == repo,
        CiMergedPullRequest.merged_at >= w.since,
        CiMergedPullRequest.merged_at < w.until,
    )
    numbers = select(CiMergedPullRequest.pr_number).where(*merged_in_window)
    branches = select(CiMergedPullRequest.head_branch).where(
        *merged_in_window, CiMergedPullRequest.head_branch.is_not(None)
    )
    runs = await _columns(
        session,
        select(
            CiWorkflowRun.pr_number,
            CiWorkflowRun.head_branch,
            CiWorkflowRun.event,
            CiWorkflowRun.created_at,
        ).where(
            CiWorkflowRun.repo == repo,
            CiWorkflowRun.workflow_path == REQUIRED_CI_PATH,
            CiWorkflowRun.conclusion.in_(VERDICTS),
            or_(
                CiWorkflowRun.pr_number.in_(numbers),
                and_(
                    CiWorkflowRun.pr_number.is_(None),
                    CiWorkflowRun.head_branch.in_(branches),
                ),
            ),
        ),
        "created_at",
    )
    return pulls, covered_from, runs


def _rounds_from(
    pulls: list[Row], covered_from: datetime | None, runs: list[list]
) -> dict[str, Any]:
    """Required CI rounds of each merged PR.

    A pull_request run names its PR only while the head branch exists, so most
    runs of a merged PR carry no number; those are matched by the PR's head
    branch, counting only runs created during the PR's lifetime so other PRs
    reusing its branch name are not counted. Queue membership is not required.
    """
    coverage = {
        "covered_from": covered_from.isoformat() if covered_from else None,
        "incomplete_prs": sum(
            covered_from is None or p.created_at < covered_from for p in pulls
        ),
    }
    if not pulls:
        return {
            "prs": 0,
            "pr_rounds": summary([]),
            "queue_rounds": summary([]),
            "more_than_one": None,
            "histogram": [],
            **coverage,
        }
    by_number = {p.pr_number: p for p in pulls}
    by_branch: dict[str, list[Row]] = defaultdict(list)
    for pull in pulls:
        if pull.head_branch:
            by_branch[pull.head_branch].append(pull)
    pr_rounds: Counter = Counter()
    queue_rounds: Counter = Counter()
    for number, branch, event, created in zip(*runs, strict=True):
        candidates = (
            [by_number[number]] if number in by_number else by_branch.get(branch, [])
        )
        for pull in candidates:
            if not pull.created_at <= created <= pull.merged_at:
                continue
            if event == "pull_request":
                pr_rounds[pull.pr_number] += 1
            elif event == "merge_group":
                queue_rounds[pull.pr_number] += 1
    prs = list(by_number)
    per_pr = [float(pr_rounds[pr]) for pr in prs]
    histogram = Counter(min(int(n), 5) for n in per_pr)
    return {
        "prs": len(prs),
        "pr_rounds": summary(per_pr),
        "queue_rounds": summary([float(queue_rounds[pr]) for pr in prs]),
        "more_than_one": sum(n > 1 for n in per_pr) / len(per_pr),
        "histogram": [{"rounds": k, "prs": histogram[k]} for k in range(0, 6)],
        **coverage,
    }


async def _sweep_status(session, repo, w: Window) -> dict[str, Any] | None:
    last = await session.scalar(
        select(CiActivitySweep)
        .where(CiActivitySweep.repo == repo)
        .order_by(CiActivitySweep.started_at.desc())
        .limit(1)
    )
    if last is None:
        return None
    return {
        "at": last.finished_at.isoformat(),
        "error": str(last.report.get("error") or ""),
        "jobs_owed": int(last.report.get("jobs_owed") or 0),
        # Which installation's counters a sampler should read for this repo.
        "installation_id": last.report.get("installation_id"),
        "request_counts": last.report.get("request_counts", {}),
        "request_scope": last.report.get("request_scope"),
        "covered_from": last.runs_listed_from.isoformat()
        if last.runs_listed_from
        else None,
        "covered_to": last.runs_listed_to.isoformat() if last.runs_listed_to else None,
        "complete": last.runs_listed_from is not None
        and last.runs_listed_from <= w.since
        and not last.report.get("error")
        and not last.report.get("truncated"),
    }


async def job_table(
    session: AsyncSession, repo: str, *, days: int, runner: str | None = None
) -> list[dict[str, Any]]:
    """Per workflow and job name: wait, duration and failures."""
    jobs = await _jobs_in(session, repo, window(days))
    return await asyncio.to_thread(_job_table, jobs, runner)


def _job_table(jobs: list[list], runner: str | None) -> list[dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for job in _each(jobs, _Job):
        if runner is not None and job.runner != runner:
            continue
        row = rows.setdefault(
            (job.workflow_path, job.name),
            {
                "name": job.name,
                "workflow": job.workflow_name,
                "workflow_path": job.workflow_path,
                "durations": [],
                "waits": [],
                "failed": 0,
                "jobs": 0,
            },
        )
        row["jobs"] += 1
        row["failed"] += int(job.conclusion == "failure")
        took = _seconds(job.started_at, job.completed_at)
        wait = _seconds(job.created_at, job.started_at)
        if took is not None and took > 0:
            row["durations"].append(took)
        if wait is not None and wait >= 0:
            row["waits"].append(wait)
    out = [
        {
            "name": r["name"],
            "workflow": r["workflow"],
            "workflow_path": r["workflow_path"],
            "jobs": r["jobs"],
            "failed": r["failed"],
            "duration_s": summary(r["durations"]),
            "wait_p50_s": percentile(r["waits"], 0.5),
            "busy_hours": sum(r["durations"]) / 3600,
        }
        for r in rows.values()
    ]
    out.sort(key=lambda r: -(r["duration_s"]["p50"] or 0))
    return out[:TABLE_LIMIT]


async def step_table(
    session: AsyncSession,
    repo: str,
    *,
    days: int,
    workflow: str,
    job: str,
    runner: str | None = None,
) -> list[dict[str, Any]]:
    """Per step of one workflow's job: how long it takes."""
    w = window(days)
    rows = await _rows(
        session,
        select(
            CiJobStep.number,
            CiJobStep.name,
            CiJobStep.started_at,
            CiJobStep.completed_at,
            CiJob.runner_kind,
            CiJob.runner_name,
            CiJob.labels,
        )
        .join(CiJob, CiJob.id == CiJobStep.job_row_id)
        .join(CiWorkflowRun, CiWorkflowRun.id == CiJob.run_row_id)
        .where(
            and_(
                CiJob.repo == repo,
                CiJob.name == job,
                CiWorkflowRun.workflow_path == workflow,
                CiJob.started_at >= w.since,
                CiJob.started_at < w.until,
            )
        ),
    )
    return await asyncio.to_thread(_step_table, rows, runner)


def _step_table(rows: list[Row], runner: str | None) -> list[dict[str, Any]]:
    steps: dict[str, dict[str, Any]] = {}
    for number, name, started, completed, kind, runner_name, labels in rows:
        if (
            runner is not None
            and runner_group(kind, runner_name, list(labels or [])) != runner
        ):
            continue
        step = steps.setdefault(name, {"name": name, "numbers": [], "durations": []})
        step["numbers"].append(number)
        took = _seconds(started, completed)
        if took is not None and took >= 0:
            step["durations"].append(took)
    out = [
        {
            "name": s["name"],
            "number": percentile([float(n) for n in s["numbers"]], 0.5),
            "duration_s": summary(s["durations"]),
        }
        for s in steps.values()
    ]
    out.sort(key=lambda s: s["number"] or 0)
    return out[:TABLE_LIMIT]
