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

import bisect
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
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
from app.domain.day_windows import utc_day_window

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
    since, until, buckets = utc_day_window(days, now or datetime.now(UTC))
    return Window(since, until, buckets)


async def _jobs_in(session: AsyncSession, repo: str, w: Window):
    """Jobs that started in the window and had a runner, with their run's event."""
    rows = await session.execute(
        select(
            CiJob.name,
            CiJob.created_at,
            CiJob.started_at,
            CiJob.completed_at,
            CiJob.conclusion,
            CiJob.runner_kind,
            CiJob.runner_name,
            CiJob.labels,
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
        )
    )
    return list(rows)


async def _required_runs(session: AsyncSession, repo: str, since: datetime, until):
    return list(
        await session.scalars(
            select(CiWorkflowRun).where(
                CiWorkflowRun.repo == repo,
                CiWorkflowRun.workflow_path == REQUIRED_CI_PATH,
                CiWorkflowRun.created_at >= since,
                CiWorkflowRun.created_at < until,
            )
        )
    )


async def _first_attempt_jobs(
    session: AsyncSession, run_row_ids: list
) -> dict[Any, tuple[set[str], datetime | None]]:
    """Per run row: (names of its first attempt's jobs, last completion among them)."""
    names: dict[Any, set[str]] = defaultdict(set)
    last: dict[Any, datetime | None] = {}
    if run_row_ids:
        rows = await session.execute(
            select(CiJob.run_row_id, CiJob.name, CiJob.completed_at).where(
                CiJob.run_row_id.in_(run_row_ids), CiJob.run_attempt == 1
            )
        )
        for run_row_id, name, completed in rows:
            names[run_row_id].add(name)
            seen = last.get(run_row_id)
            if completed is not None and (seen is None or completed > seen):
                last[run_row_id] = completed
    return defaultdict(
        lambda: (set(), None), {k: (v, last.get(k)) for k, v in names.items()}
    )


async def _failed_before_last(session: AsyncSession, run_row_ids: list) -> dict:
    """Per run row: the names of jobs that failed on an attempt before the
    run's latest, and the names that succeeded on a later attempt."""
    out: dict = defaultdict(lambda: (set(), set()))
    if not run_row_ids:
        return out
    rows = await session.execute(
        select(CiJob.run_row_id, CiJob.name, CiJob.run_attempt, CiJob.conclusion).where(
            CiJob.run_row_id.in_(run_row_ids)
        )
    )
    attempts: dict = defaultdict(list)
    for run_row_id, name, attempt, conclusion in rows:
        attempts[run_row_id].append((name, attempt, conclusion))
    for run_row_id, jobs in attempts.items():
        failed = {(n, a) for n, a, c in jobs if c == "failure"}
        passed = {(n, a) for n, a, c in jobs if c == "success"}
        flaky = {n for n, a in failed if any(pn == n and pa > a for pn, pa in passed)}
        out[run_row_id] = ({n for n, _ in failed}, flaky)
    return out


async def _queue_passages(session: AsyncSession, repo: str, w: Window):
    """Each PR merged through the queue in the window: (pr, first added, merged)."""
    merged = aliased(CiMergeQueueEvent)
    rows = await session.execute(
        select(merged.pr_number, merged.at).where(
            merged.repo == repo,
            merged.kind == "removed",
            merged.reason == MERGED,
            merged.at >= w.since,
            merged.at < w.until,
        )
    )
    merges = {pr: at for pr, at in rows}
    if not merges:
        return []
    adds = await session.execute(
        select(CiMergeQueueEvent.pr_number, CiMergeQueueEvent.at).where(
            CiMergeQueueEvent.repo == repo,
            CiMergeQueueEvent.kind == "added",
            CiMergeQueueEvent.pr_number.in_(list(merges)),
        )
    )
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


def _minute_samples(spans: Iterable[tuple[datetime, datetime]], w: Window, until):
    """How many spans cover each minute from the window's start to ``until``."""
    starts = sorted(a for a, _ in spans)
    ends = sorted(b for _, b in spans)
    samples = []
    moment = w.since
    while moment < until:
        samples.append(
            bisect.bisect_right(starts, moment) - bisect.bisect_right(ends, moment)
        )
        moment += timedelta(minutes=1)
    return samples


async def build_board(
    session: AsyncSession, repo: str, *, days: int, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    w = window(days, now)
    sampled_until = min(now, w.until)

    # --- runners: wait, busy time, per machine, concurrency -------------------
    jobs = await _jobs_in(session, repo, w)
    waits: dict[str, list[float]] = defaultdict(list)
    wait_by_day: dict[tuple[str, date], list[float]] = defaultdict(list)
    machines: dict[str, dict[str, Any]] = {}
    spans: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    busy_seconds: dict[str, float] = defaultdict(float)
    for job in jobs:
        wait = _seconds(job.created_at, job.started_at)
        took = _seconds(job.started_at, job.completed_at)
        kind = job.runner_kind
        group = runner_group(kind, job.runner_name, list(job.labels or []))
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
            spans[cls].append((job.started_at, end))
            spans[kind].append((job.started_at, end))

    per_class = {cls: _minute_samples(spans[cls], w, sampled_until) for cls in CLASSES}
    per_kind = {
        k: _minute_samples(spans[k], w, sampled_until)
        for k in ("hosted", "self_hosted")
    }
    total = [sum(v[i] for v in per_class.values()) for i in range(len(per_class["pr"]))]

    # --- the merge queue -----------------------------------------------------
    passages = await _queue_passages(session, repo, w)
    queue_minutes = [(m - a).total_seconds() / 60 for _, a, m, _ in passages]
    queue_by_day: dict[date, list[float]] = defaultdict(list)
    for _, added, merged_at, _ in passages:
        queue_by_day[_day(merged_at)].append((merged_at - added).total_seconds() / 60)
    depth = await _queue_depth(session, repo, w, sampled_until)
    ejections = await _ejections(session, repo, w)

    # --- Required CI: duration, rounds, fail-then-pass -------------------------
    runs = await _required_runs(session, repo, w.since, w.until)
    first = await _first_attempt_jobs(session, [r.id for r in runs])
    durations: dict[str, list[float]] = defaultdict(list)
    duration_by_day: dict[tuple[str, date], list[float]] = defaultdict(list)
    for run in runs:
        names, last_done = first[run.id]
        if run.conclusion not in VERDICTS or FULL_TIER_JOB not in names:
            continue
        took = _seconds(run.created_at, last_done)
        if took is None or took <= 0:
            continue
        durations[run.event].append(took / 60)
        duration_by_day[(run.event, _day(run.created_at))].append(took / 60)

    rounds = await _rounds(session, repo, w)
    retried = [r for r in runs if r.run_attempt > 1]
    failed_before = await _failed_before_last(session, [r.id for r in retried])
    verdict_runs = [r for r in runs if r.conclusion in VERDICTS]
    fail_then_pass = [
        r for r in retried if r.conclusion == "success" and failed_before[r.id][0]
    ]

    hourly = _hourly(w, sampled_until, total, per_class, per_kind, depth)
    return {
        "repo": repo,
        "days": days,
        "since": w.since.isoformat(),
        "until": w.until.isoformat(),
        "generated_at": now.isoformat(),
        "sweep": await _sweep_status(session, repo, w),
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
                "of": len(verdict_runs),
                "share": (len(fail_then_pass) / len(verdict_runs))
                if verdict_runs
                else None,
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


async def _queue_depth(session, repo, w: Window, until) -> list[int]:
    """PRs in the merge queue at each minute of the window."""
    rows = await session.execute(
        select(
            CiMergeQueueEvent.pr_number, CiMergeQueueEvent.kind, CiMergeQueueEvent.at
        )
        .where(CiMergeQueueEvent.repo == repo, CiMergeQueueEvent.at < until)
        .order_by(CiMergeQueueEvent.at)
    )
    entered: dict[int, datetime] = {}
    spans: list[tuple[datetime, datetime]] = []
    for pr, kind, at in rows:
        if kind == "added":
            entered.setdefault(pr, at)
        elif pr in entered:
            spans.append((entered.pop(pr), at))
    spans.extend((at, until) for at in entered.values())
    return _minute_samples(spans, w, until)


async def _ejections(session, repo, w: Window) -> list[dict[str, Any]]:
    rows = await session.execute(
        select(CiMergeQueueEvent.reason, CiMergeQueueEvent.actor).where(
            CiMergeQueueEvent.repo == repo,
            CiMergeQueueEvent.kind == "removed",
            CiMergeQueueEvent.at >= w.since,
            CiMergeQueueEvent.at < w.until,
        )
    )
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
    """Required CI rounds of each merged PR.

    A pull_request run names its PR only while the head branch exists, so most
    runs of a merged PR carry no number; those are matched by the PR's head
    branch, counting only runs created during the PR's lifetime so other PRs
    reusing its branch name are not counted. Queue membership is not required.
    """
    pulls = list(
        await session.scalars(
            select(CiMergedPullRequest).where(
                CiMergedPullRequest.repo == repo,
                CiMergedPullRequest.merged_at >= w.since,
                CiMergedPullRequest.merged_at < w.until,
            )
        )
    )
    last = await session.scalar(
        select(CiActivitySweep)
        .where(CiActivitySweep.repo == repo)
        .order_by(CiActivitySweep.started_at.desc())
        .limit(1)
    )
    covered_from = last.runs_listed_from if last else None
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
    by_branch: dict[str, list[CiMergedPullRequest]] = defaultdict(list)
    for pull in pulls:
        if pull.head_branch:
            by_branch[pull.head_branch].append(pull)
    rows = await session.execute(
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
                CiWorkflowRun.pr_number.in_(list(by_number)),
                and_(
                    CiWorkflowRun.pr_number.is_(None),
                    CiWorkflowRun.head_branch.in_(list(by_branch)),
                ),
            ),
        )
    )
    pr_rounds: Counter = Counter()
    queue_rounds: Counter = Counter()
    for number, branch, event, created in rows:
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
    w = window(days)
    jobs = await _jobs_in(session, repo, w)
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for job in jobs:
        group = runner_group(job.runner_kind, job.runner_name, list(job.labels or []))
        if runner is not None and group != runner:
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
    rows = await session.execute(
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
        )
    )
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
