"""One sweep: bring the CI activity tables up to date with GitHub.

Four steps, each bounded so one sweep spends a predictable number of requests
out of the installation's hourly quota (the quota every other background job
and every person delivering a card share):

1. **Recent runs.** List the runs created since the earliest of: `RECENT` ago,
   the oldest stored run that has not completed (up to `OPEN_LOOKBACK` back),
   and where the last sweep's listing ended (`runs_listed_to`) — and upsert
   them. A run changes after it is first listed (it starts, finishes, is
   re-run), almost always within the hour, so re-listing a trailing window is
   what keeps status, conclusion and attempt current; reaching back to the
   last listing is what keeps a deployment that was down from leaving a hole.
   A run re-run after it fell out of that window keeps its older figures.
2. **Backfill.** Walk the listing back from where the last sweep left it
   (`runs_listed_from`) towards the window's start, `BACKFILL_SLICE` at a time,
   at most `backfill_slices` per sweep. A new deployment fills the window over
   its first sweeps; after that this step does nothing.
3. **Jobs.** Read the jobs of completed runs whose stored jobs are behind the
   run's attempt, newest first, at most `jobs_budget` runs per sweep. A run
   whose conclusion is `skipped` used no runner; its jobs are not read.
4. **Pull requests and merge queue.** Read merge facts and queue events of
   every PR updated since the last sweep's `queue_read_to` (the window's start
   on the first sweep), one
   `QUEUE_SLICE` of update time per search.

Then everything older than the window is pruned and the sweep is recorded with
its report and its two cursors.

**A sweep that could not run reports instead of raising**, for the reason the
ratchet ingest gives: the caller is a periodic job with nowhere to put a
traceback. No installation, a refused mint, the quota kept for people, a quota
refusal mid-sweep, a GitHub error, a database error — each ends the sweep where
it stands, keeps what was stored, and says why in ``report["error"]``. A run
GitHub no longer has (404 on its jobs) is marked read with no jobs rather than
stopping every later sweep at the same place. Every sweep is recorded,
the ones that read nothing included, so the page can say why its numbers stopped.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.ci_activity.github import (
    CiGitHubError,
    GitHubCi,
    Listed,
    QueueEvent,
)
from app.domain.ci_activity.store import CiActivityStore
from app.domain.project.forge import github_read_token_for_repo

#: How much history is kept and read; older rows are deleted by the sweep.
WINDOW = timedelta(days=30)
#: Runs whose jobs one sweep reads (one request each, two for a run of over 100
#: jobs). Every five minutes, with the run listing and the queue search beside
#: it, that is about 1000-1300 requests an hour while a new deployment backfills
#: the window (about a day) and under 100 an hour after, out of the
#: installation's hourly quota every background job shares.
JOBS_PER_SWEEP = 80
#: Older run slices one sweep lists while backfilling: a day per sweep.
BACKFILL_SLICES_PER_SWEEP = 6
#: How far back every sweep re-lists runs to catch their status changing.
#: `rerun-failed.yml` re-runs a failed run within minutes, inside this.
RECENT = timedelta(hours=3)
#: How old a still-open run may be and still pull the listing back to it. A
#: job cannot run longer than six hours; a run open past a day is stuck, and
#: re-listing a day of runs every sweep for it is not worth it.
OPEN_LOOKBACK = timedelta(hours=24)
#: One backfill listing's span of run-creation time. Four hours held under
#: GitHub's 1000-run cap on this repository's busiest day (~2000 runs);
#: `list_runs` splits a span that does not.
BACKFILL_SLICE = timedelta(hours=4)
#: One queue search's span of PR-update time.
QUEUE_SLICE = timedelta(days=1)
#: How far before the last sweep's queue cursor the next one starts, so a PR
#: updated while the previous sweep was reading is not missed.
QUEUE_OVERLAP = timedelta(minutes=10)


class CiSource(Protocol):
    async def list_runs(
        self, *, owner: str, repo: str, token: str, since: datetime, until: datetime
    ) -> Listed: ...

    async def run_jobs(
        self, *, owner: str, repo: str, token: str, run_id: int
    ) -> Listed: ...

    async def queue_events(
        self, *, owner: str, repo: str, token: str, since: datetime, until: datetime
    ) -> tuple[list[QueueEvent], Listed]: ...


async def sweep(sessions: SessionFactory) -> dict[str, Any]:
    """Open a session and sweep — what the periodic job calls."""
    async with sessions() as session:
        return await sweep_once(session)


async def sweep_once(
    session: AsyncSession,
    *,
    source: CiSource | None = None,
    repo: str | None = None,
    now: datetime | None = None,
    jobs_budget: int | None = None,
    backfill_slices: int | None = None,
) -> dict[str, Any]:
    repo = repo or settings.platform_repository
    now = now or datetime.now(UTC)
    jobs_budget = JOBS_PER_SWEEP if jobs_budget is None else jobs_budget
    backfill_slices = (
        BACKFILL_SLICES_PER_SWEEP if backfill_slices is None else backfill_slices
    )
    window_start = now - WINDOW
    report: dict[str, Any] = {
        "repo": repo,
        "requests": 0,
        "runs_upserted": 0,
        "jobs_read": 0,
        "jobs_owed": 0,
        "queue_events_added": 0,
        "merged_prs_upserted": 0,
        "pruned_runs": 0,
        "gone_runs": 0,
        "truncated": [],
        "error": "",
    }
    owner, _, name = repo.partition("/")
    if not owner or not name:
        # i18n-exempt: internal sweep report, read on the admin page only
        report["error"] = f"not an owner/repo name: {repo!r}"
        return report

    store = CiActivityStore(session)
    last = await store.last_sweep(repo)
    runs_listed_from = last.runs_listed_from if last else None
    runs_listed_to = last.runs_listed_to if last else None
    queue_read_to = last.queue_read_to if last else None
    credential = await github_read_token_for_repo(repo, session, background=True)
    if credential.token is None:
        report["error"] = credential.reason
        await store.record_sweep(
            repo,
            started_at=now,
            runs_listed_from=runs_listed_from,
            runs_listed_to=runs_listed_to,
            queue_read_to=queue_read_to,
            report=report,
        )
        return report
    token = credential.token
    reader: CiSource = source or GitHubCi()

    def spent(listed: Listed, what: str) -> None:
        report["requests"] += listed.requests
        if listed.truncated:
            report["truncated"].append(what)

    try:
        recent_from = now - RECENT
        oldest_open = await store.oldest_open_run(repo, now - OPEN_LOOKBACK)
        for earlier in (oldest_open, runs_listed_to):
            if earlier is not None:
                recent_from = min(recent_from, earlier)
        recent_from = max(window_start, recent_from)
        listed = await reader.list_runs(
            owner=owner, repo=name, token=token, since=recent_from, until=now
        )
        spent(listed, "recent runs")
        report["runs_upserted"] += await store.upsert_runs(repo, listed.items)
        runs_listed_to = now
        if runs_listed_from is None or runs_listed_from > recent_from:
            runs_listed_from = recent_from

        for _ in range(backfill_slices):
            if runs_listed_from <= window_start:
                break
            since = max(window_start, runs_listed_from - BACKFILL_SLICE)
            listed = await reader.list_runs(
                owner=owner, repo=name, token=token, since=since, until=runs_listed_from
            )
            spent(listed, f"runs from {since.isoformat()}")
            report["runs_upserted"] += await store.upsert_runs(repo, listed.items)
            runs_listed_from = since

        for run in await store.runs_owing_jobs(repo, jobs_budget):
            if run.conclusion == "skipped":
                await store.replace_jobs(run, [], attempt=run.run_attempt)
                continue
            try:
                listed = await reader.run_jobs(
                    owner=owner, repo=name, token=token, run_id=run.run_id
                )
            except CiGitHubError as exc:
                if exc.status != 404:
                    raise
                # The run was deleted on GitHub. Left owed, it would be the
                # newest owed run on every sweep and stop each one here.
                await store.replace_jobs(run, [], attempt=run.run_attempt)
                report["gone_runs"] += 1
                continue
            spent(listed, f"jobs of run {run.run_id}")
            report["jobs_read"] += await store.replace_jobs(
                run, listed.items, attempt=run.run_attempt
            )

        cursor = max(window_start, (queue_read_to or window_start) - QUEUE_OVERLAP)
        while cursor < now:
            until = min(now, cursor + QUEUE_SLICE)
            events, listed = await reader.queue_events(
                owner=owner, repo=name, token=token, since=cursor, until=until
            )
            spent(listed, f"queue from {cursor.isoformat()}")
            report["merged_prs_upserted"] += await store.upsert_merged_pull_requests(
                repo, listed.items
            )
            report["queue_events_added"] += await store.add_queue_events(repo, events)
            cursor = queue_read_to = until
    except (CiGitHubError, httpx.HTTPError, SQLAlchemyError) as exc:
        await session.rollback()
        # i18n-exempt: GitHub's own words, for the admin sweep report
        report["error"] = f"{type(exc).__name__}: {exc}"

    report["pruned_runs"] = await store.prune(repo, window_start)
    if runs_listed_from is not None:
        # Pruning removes coverage even when backfill once reached further back.
        runs_listed_from = max(window_start, runs_listed_from)
    report["jobs_owed"] = await store.count_runs_owing_jobs(repo)
    await store.record_sweep(
        repo,
        started_at=now,
        runs_listed_from=runs_listed_from,
        runs_listed_to=runs_listed_to,
        queue_read_to=queue_read_to,
        report=report,
    )
    return report
