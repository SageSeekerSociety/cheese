"""Writing the CI activity tables — the only code that does.

Runs are upserted (a run's status, conclusion and attempt change after it is
first listed). A run's jobs are replaced whole when they are read, because a
re-run adds jobs to a run and GitHub's answer is the complete list. Queue events
are inserted once each. Everything older than the window is deleted by `prune`:
this is a picture of recent CI, not an archive.

Nothing here commits except `record_sweep`. Each write is one savepoint, so a
write that fails leaves the ones before it standing, and a sweep's writes become
visible to readers in the same commit as its `ci_activity_sweeps` row: whatever
the tables hold is what the latest recorded sweep left there. A sweep that never
reaches its record — its process dies, an error nobody catches — leaves nothing
behind, and the next sweep reads the same ground again.
"""

from __future__ import annotations

import functools
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Concatenate

from sqlalchemy import ColumnElement, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.domain.ci_activity.github import QueueEvent, parse_time
from app.domain.ci_activity.models import (
    CiActivitySweep,
    CiJob,
    CiJobStep,
    CiMergedPullRequest,
    CiMergeQueueEvent,
    CiWorkflowRun,
)

#: `gh-readonly-queue/<base>/pr-<number>-<sha>`: the branch GitHub's merge queue
#: builds a group on. The only place a merge_group run names its PR.
_QUEUE_BRANCH = re.compile(r"^gh-readonly-queue/.+/pr-(\d+)-[0-9a-f]+$")

#: The runner group GitHub's own runners are in.
HOSTED_GROUP = "GitHub Actions"

#: Rows one INSERT carries. asyncpg refuses a statement with more than 32767
#: bound values: a run row binds 18 (at most 1820 runs a statement; a sweep after
#: a day of downtime lists about 2000), a queue event 8 (4095; a busy day here
#: has had 2659).
UPSERT_BATCH = 500


def _now() -> datetime:
    return datetime.now(UTC)


def sweep_lock(repo: str) -> ColumnElement[int]:
    """The advisory lock a sweep of ``repo`` holds exclusively from before its
    first write until its record has landed (`sweeping`). A reader that holds it
    shared sees tables no sweep is part-way through writing."""
    return func.hashtextextended(f"ci-activity-sweep:{repo}", 0)


@asynccontextmanager
async def sweeping(session: AsyncSession, repo: str) -> AsyncIterator[None]:
    """Hold ``repo``'s sweep lock for the whole of one sweep.

    A sweep commits as it goes, so the lock cannot live in its transactions: it
    is a session-level lock on a connection of its own, waiting out any reader
    that holds it shared."""
    bind = session.bind
    assert bind is not None
    engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    connection = await engine.connect()
    try:
        await connection.execute(select(func.pg_advisory_lock(sweep_lock(repo))))
        await connection.commit()
        yield
    finally:
        # Dropped rather than unlocked and pooled: a session-level lock outlives
        # a pool's reset-on-return, so a failed unlock would leave the next
        # sweep waiting on a pooled connection forever. Closing the connection
        # always lets it go.
        await connection.invalidate()
        await connection.close()


def pr_number_of(run: dict[str, Any]) -> int | None:
    """The PR a run tested, or None when the run does not say.

    A pull_request run lists its PR only while the head branch exists, so most
    runs of merged PRs come back with none; the board matches those by branch
    (`CiMergeQueueEvent.head_branch`)."""
    if run.get("event") == "merge_group":
        match = _QUEUE_BRANCH.match(str(run.get("head_branch") or ""))
        return int(match.group(1)) if match else None
    for pull in run.get("pull_requests") or []:
        if isinstance(pull, dict) and isinstance(pull.get("number"), int):
            return pull["number"]
    return None


def run_row(repo: str, run: dict[str, Any]) -> dict[str, Any] | None:
    """The row for one listed run, or None for an entry that is not a run."""
    run_id = run.get("id")
    created = parse_time(run.get("created_at"))
    if not isinstance(run_id, int) or created is None:
        return None
    return {
        "repo": repo,
        "run_id": run_id,
        "workflow_id": int(run.get("workflow_id") or 0),
        "workflow_name": str(run.get("name") or "")[:300],
        "workflow_path": str(run.get("path") or "")[:300],
        "event": str(run.get("event") or "")[:40],
        "head_branch": (
            str(run["head_branch"])[:300] if run.get("head_branch") else None
        ),
        "head_sha": str(run.get("head_sha") or "")[:64],
        "pr_number": pr_number_of(run),
        "run_attempt": int(run.get("run_attempt") or 1),
        "status": str(run.get("status") or "")[:20],
        "conclusion": (str(run["conclusion"])[:20] if run.get("conclusion") else None),
        "created_at": created,
        "run_started_at": parse_time(run.get("run_started_at")),
        "updated_at": parse_time(run.get("updated_at")),
        "html_url": (str(run["html_url"])[:500] if run.get("html_url") else None),
    }


def runner_kind(job: dict[str, Any]) -> str | None:
    if not job.get("runner_name"):
        return None
    return "hosted" if job.get("runner_group_name") == HOSTED_GROUP else "self_hosted"


def _savepoint[**P, R](
    write: Callable[Concatenate[CiActivityStore, P], Awaitable[R]],
) -> Callable[Concatenate[CiActivityStore, P], Awaitable[R]]:
    """Run one write as a savepoint: all of it stands, or none of it."""

    @functools.wraps(write)
    async def run(self: CiActivityStore, *args: P.args, **kwargs: P.kwargs) -> R:
        async with self._session.begin_nested():
            return await write(self, *args, **kwargs)

    return run


class CiActivityStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @_savepoint
    async def upsert_merged_pull_requests(
        self, repo: str, pulls: list[dict[str, Any]]
    ) -> int:
        """Keep merge facts from the same PR search that supplies queue events."""
        rows = {}
        for pull in pulls:
            number = pull.get("number")
            created = parse_time(pull.get("createdAt"))
            merged = parse_time(pull.get("mergedAt"))
            if not isinstance(number, int) or created is None or merged is None:
                continue
            branch = pull.get("headRefName")
            rows[number] = {
                "repo": repo,
                "pr_number": number,
                "head_branch": str(branch)[:300] if branch else None,
                "created_at": created,
                "merged_at": merged,
            }
        values = list(rows.values())
        for start in range(0, len(values), UPSERT_BATCH):
            stmt = insert(CiMergedPullRequest).values(
                values[start : start + UPSERT_BATCH]
            )
            await self._session.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_ci_merged_pull_requests_repo_pr_number",
                    set_={
                        c: stmt.excluded[c]
                        for c in ("head_branch", "created_at", "merged_at")
                    },
                )
            )
        return len(rows)

    @_savepoint
    async def upsert_runs(self, repo: str, runs: list[dict[str, Any]]) -> int:
        """Insert new runs and refresh known ones; return how many rows changed.

        ``jobs_attempt`` is never touched here: only a jobs read moves it.
        """
        rows = [
            r
            for r in {
                r["run_id"]: r for r in (run_row(repo, run) for run in runs) if r
            }.values()
        ]
        changed = 0
        for start in range(0, len(rows), UPSERT_BATCH):
            changed += await self._upsert(rows[start : start + UPSERT_BATCH])
        return changed

    async def _upsert(self, rows: list[dict[str, Any]]) -> int:
        now = _now()
        stmt = insert(CiWorkflowRun).values(
            [{**row, "ingested_at": now} for row in rows]
        )
        refreshed = (
            "workflow_name",
            "event",
            "head_branch",
            "head_sha",
            "pr_number",
            "run_attempt",
            "status",
            "conclusion",
            "run_started_at",
            "updated_at",
            "html_url",
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_ci_workflow_runs_repo_run_id",
            set_={**{c: stmt.excluded[c] for c in refreshed}, "ingested_at": now},
        )
        result = await self._session.execute(stmt)
        # rowcount exists on the CursorResult execute() returns for DML at runtime
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def oldest_open_run(self, repo: str, since: datetime) -> datetime | None:
        """When the oldest run created since ``since`` that has not completed
        was created: the listing has to reach back that far to see it finish."""
        return await self._session.scalar(
            select(func.min(CiWorkflowRun.created_at)).where(
                CiWorkflowRun.repo == repo,
                CiWorkflowRun.status != "completed",
                CiWorkflowRun.created_at >= since,
            )
        )

    async def runs_owing_jobs(self, repo: str, limit: int) -> list[CiWorkflowRun]:
        """Completed runs whose stored jobs are not their latest attempt's,
        newest first: the page fills from the present backwards."""
        rows = await self._session.scalars(
            select(CiWorkflowRun)
            .where(
                CiWorkflowRun.repo == repo,
                CiWorkflowRun.status == "completed",
                or_(
                    CiWorkflowRun.jobs_attempt.is_(None),
                    CiWorkflowRun.jobs_attempt != CiWorkflowRun.run_attempt,
                ),
            )
            .order_by(CiWorkflowRun.created_at.desc())
            .limit(limit)
        )
        return list(rows)

    async def count_runs_owing_jobs(self, repo: str) -> int:
        total = await self._session.scalar(
            select(func.count())
            .select_from(CiWorkflowRun)
            .where(
                CiWorkflowRun.repo == repo,
                CiWorkflowRun.status == "completed",
                or_(
                    CiWorkflowRun.jobs_attempt.is_(None),
                    CiWorkflowRun.jobs_attempt != CiWorkflowRun.run_attempt,
                ),
            )
        )
        return int(total or 0)

    @_savepoint
    async def replace_jobs(
        self, run: CiWorkflowRun, jobs: list[dict[str, Any]], *, attempt: int
    ) -> int:
        """Store one run's jobs and their steps, replacing what was there, and
        mark the run as read at ``attempt``. Returns how many jobs were stored."""
        await self._session.execute(delete(CiJob).where(CiJob.run_row_id == run.id))
        stored = 0
        for job in jobs:
            job_id = job.get("id")
            if not isinstance(job_id, int):
                continue
            row = CiJob(
                run_row_id=run.id,
                repo=run.repo,
                job_id=job_id,
                run_attempt=int(job.get("run_attempt") or 1),
                name=str(job.get("name") or "")[:300],
                status=str(job.get("status") or "")[:20],
                conclusion=(
                    str(job["conclusion"])[:20] if job.get("conclusion") else None
                ),
                created_at=parse_time(job.get("created_at")),
                started_at=parse_time(job.get("started_at")),
                completed_at=parse_time(job.get("completed_at")),
                runner_name=(
                    str(job["runner_name"])[:200] if job.get("runner_name") else None
                ),
                runner_kind=runner_kind(job),
                labels=[str(label)[:200] for label in job.get("labels") or []],
            )
            self._session.add(row)
            await self._session.flush()
            steps = [
                CiJobStep(
                    job_row_id=row.id,
                    number=int(step["number"]),
                    name=str(step.get("name") or "")[:300],
                    conclusion=(
                        str(step["conclusion"])[:20] if step.get("conclusion") else None
                    ),
                    started_at=parse_time(step.get("started_at")),
                    completed_at=parse_time(step.get("completed_at")),
                )
                for step in job.get("steps") or []
                if isinstance(step, dict)
                and isinstance(step.get("number"), int)
                and step.get("conclusion") != "skipped"
            ]
            self._session.add_all(steps)
            stored += 1
        await self._session.execute(
            update(CiWorkflowRun)
            .where(CiWorkflowRun.id == run.id)
            .values(jobs_attempt=attempt)
        )
        return stored

    @_savepoint
    async def add_queue_events(self, repo: str, events: list[QueueEvent]) -> int:
        inserted = 0
        for start in range(0, len(events), UPSERT_BATCH):
            inserted += await self._add_events(
                repo, events[start : start + UPSERT_BATCH]
            )
        return inserted

    async def _add_events(self, repo: str, events: list[QueueEvent]) -> int:
        result = await self._session.execute(
            insert(CiMergeQueueEvent)
            .values(
                [
                    {
                        "repo": repo,
                        "pr_number": e.pr_number,
                        "kind": e.kind,
                        "at": e.at,
                        "reason": e.reason[:60] if e.reason else None,
                        "actor": e.actor[:100] if e.actor else None,
                        "head_branch": e.head_branch[:300] if e.head_branch else None,
                    }
                    for e in events
                ]
            )
            .on_conflict_do_nothing(constraint="uq_ci_merge_queue_events_event")
            .returning(CiMergeQueueEvent.id)
        )
        return len(list(result))

    async def last_sweep(self, repo: str) -> CiActivitySweep | None:
        return await self._session.scalar(
            select(CiActivitySweep)
            .where(CiActivitySweep.repo == repo)
            .order_by(CiActivitySweep.started_at.desc())
            .limit(1)
        )

    async def record_sweep(
        self,
        repo: str,
        *,
        started_at: datetime,
        runs_listed_from: datetime | None,
        runs_listed_to: datetime | None,
        queue_read_to: datetime | None,
        report: dict[str, Any],
    ) -> None:
        self._session.add(
            CiActivitySweep(
                repo=repo,
                started_at=started_at,
                finished_at=_now(),
                runs_listed_from=runs_listed_from,
                runs_listed_to=runs_listed_to,
                queue_read_to=queue_read_to,
                report=report,
            )
        )
        await self._session.commit()

    @_savepoint
    async def prune(self, repo: str, before: datetime) -> int:
        """Delete everything older than ``before``; jobs and steps go with
        their run. Returns how many runs were deleted."""
        runs = await self._session.execute(
            delete(CiWorkflowRun).where(
                CiWorkflowRun.repo == repo, CiWorkflowRun.created_at < before
            )
        )
        await self._session.execute(
            delete(CiMergeQueueEvent).where(
                CiMergeQueueEvent.repo == repo, CiMergeQueueEvent.at < before
            )
        )
        await self._session.execute(
            delete(CiMergedPullRequest).where(
                CiMergedPullRequest.repo == repo, CiMergedPullRequest.merged_at < before
            )
        )
        await self._session.execute(
            delete(CiActivitySweep).where(
                CiActivitySweep.repo == repo, CiActivitySweep.started_at < before
            )
        )
        # rowcount exists on the CursorResult execute() returns for DML at runtime
        return int(runs.rowcount or 0)  # type: ignore[attr-defined]
