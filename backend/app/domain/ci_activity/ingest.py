"""Bring the CI activity tables up to date without spending people's quota.

New runs are discovered in fixed five-minute creation-time windows. The same
windows are revalidated across the existing three-hour range, extended to old
open runs up to a day back: completion and reruns do not change created_at.
Their stable URLs can earn free 304s instead of re-downloading a rolling list.
Reruns outside that existing range remain outside this collector's coverage.

Historical backfill and the per-run jobs-attempt ledger are preserved. PR merge
facts still advance by update time, without fetching queue timelines.

A sweep works in units — one listing slice, one run's jobs, one slice of merged
PRs — and commits each unit together with the cursors it advanced, recorded on
the sweep's own `ci_activity_sweeps` row. Readers therefore only ever see whole
units, and every unit a sweep finished stays finished: a failed, truncated or
deferred read, the sweep's deadline, or the process dying drops only the unit in
progress, and the next sweep resumes from the cursors last committed. Every
sweep, including a deferred one, records its reason and actual reader counts.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.ci_activity.github import (
    RESULT_CAP,
    RUNS_PAGE,
    CiGitHubError,
    GitHubCi,
    Listed,
)
from app.domain.ci_activity.models import CiActivitySweep
from app.domain.ci_activity.store import CiActivityStore, sweeping
from app.domain.project.forge import github_read_token_for_repo

WINDOW = timedelta(days=30)
#: This bounds runs, not HTTP pages: a large all-attempts jobs read can page.
JOBS_PER_SWEEP = 80
BACKFILL_SLICES_PER_SWEEP = 6
RECENT = timedelta(hours=3)
OPEN_LOOKBACK = timedelta(hours=24)
RUN_SLICE = timedelta(minutes=5)
BACKFILL_SLICE = timedelta(hours=4)
MERGED_PRS_SLICE = timedelta(days=1)
MERGED_PRS_OVERLAP = timedelta(minutes=10)
#: The longest one sweep may run before it stops after its last finished unit:
#: while it runs it holds the sweep lock (no answer is kept for the page) and two
#: pooled connections. Sized from dev's recorded sweeps, 2026-10-18 to 10-31:
#: p99 142 s over 3083 sweeps, 247 s over the 79 that finished without an
#: error, longest 371 s (the first, a 30-day backfill). A backlog longer than
#: this is worked off over several sweeps: a bulk listing commits one page at a
#: time (`read_runs`), so every sweep finishes at least one unit.
SWEEP_DEADLINE = timedelta(minutes=10)
#: Server-side backstops on each of the sweep's transactions, for a process that
#: cannot end one itself (a wedged event loop never fires `SWEEP_DEADLINE`): no
#: statement of a sweep runs longer than a minute, and the server ends a
#: transaction once it has sat idle as long as a whole sweep may take.
STATEMENT_TIMEOUT = "60s"
IDLE_IN_TRANSACTION_TIMEOUT = f"{int(SWEEP_DEADLINE.total_seconds())}s"
#: How long the statement that writes a sweep's outcome may take, deadline
#: spent or not; past it the sweep gives up and its row stays unfinished.
OUTCOME_DEADLINE = timedelta(seconds=30)
#: Report counters a unit adds to, put back to the last commit's when a unit is
#: dropped.
_COUNTERS = ("runs_upserted", "jobs_read", "merged_prs_upserted", "gone_runs")


class CiSource(Protocol):
    @property
    def request_counts(self) -> dict[str, Any]: ...

    def check_quota(self, token: str) -> None: ...

    def run_leaves(
        self,
        *,
        owner: str,
        repo: str,
        token: str,
        since: datetime,
        until: datetime,
        per_leaf: int,
        oldest_first: bool,
    ) -> AsyncIterator[tuple[datetime, datetime, Listed]]: ...

    async def run_jobs(
        self, *, owner: str, repo: str, token: str, run_id: int
    ) -> Listed: ...

    async def merged_pull_requests(
        self, *, owner: str, repo: str, token: str, since: datetime, until: datetime
    ) -> Listed: ...


def _slice_start(moment: datetime) -> datetime:
    width = int(RUN_SLICE.total_seconds())
    seconds = int(moment.timestamp())
    return datetime.fromtimestamp(seconds - seconds % width, UTC)


def _request_delta(start: dict[str, Any], end: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: value - start.get(key, 0)
        for key, value in end.items()
        if key != "by_endpoint"
    }
    before = start.get("by_endpoint", {})
    result["by_endpoint"] = {
        endpoint: count - before.get(endpoint, 0)
        for endpoint, count in end.get("by_endpoint", {}).items()
    }
    return result


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
    """One sweep, under the repo's sweep lock: `board.once_per_sweep` keeps
    answers only while no sweep holds it."""
    repo = repo or settings.platform_repository
    async with sweeping(session, repo):
        return await _sweep_once(
            session,
            reader=source or GitHubCi(),
            repo=repo,
            now=now or datetime.now(UTC),
            jobs_budget=JOBS_PER_SWEEP if jobs_budget is None else jobs_budget,
            backfill_slices=(
                BACKFILL_SLICES_PER_SWEEP
                if backfill_slices is None
                else backfill_slices
            ),
        )


def _stopped_by(exc: BaseException) -> str:
    """The sweep report's words for what ended a sweep early."""
    if isinstance(exc, TimeoutError):
        # i18n-exempt: internal sweep report, read on the admin page only
        return (
            f"Sweep stopped at its {SWEEP_DEADLINE.total_seconds():g} s deadline;"
            " the units it finished are kept"
        )
    # i18n-exempt: GitHub's own words, for the admin sweep report
    return f"{type(exc).__name__}: {exc}"


async def _limit_statements(session: AsyncSession, seconds: float) -> None:
    """No statement of the current transaction runs longer than ``seconds``."""
    await session.execute(
        select(
            func.set_config(
                "statement_timeout", f"{max(1, int(seconds * 1000))}ms", True
            )
        )
    )


async def _bound_transaction(session: AsyncSession) -> None:
    """Start the next transaction with the server-side backstops. Local to it:
    the pooled connection goes back without them."""
    for setting, value in (
        ("statement_timeout", STATEMENT_TIMEOUT),
        ("idle_in_transaction_session_timeout", IDLE_IN_TRANSACTION_TIMEOUT),
    ):
        await session.execute(select(func.set_config(setting, value, True)))


async def _sweep_once(
    session: AsyncSession,
    *,
    reader: CiSource,
    repo: str,
    now: datetime,
    jobs_budget: int,
    backfill_slices: int,
) -> dict[str, Any]:
    window_start = now - WINDOW
    before = reader.request_counts
    report: dict[str, Any] = {
        "repo": repo,
        # Which installation's quota this sweep's requests were counted against.
        # The public forge connection names a repo, never this id, so a sampler
        # could not otherwise tell which installation to read from /metrics.
        "installation_id": None,
        "requests": 0,
        "request_counts": _request_delta(before, before),
        "request_scope": "reader",
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

    await _bound_transaction(session)
    store = CiActivityStore(session)
    last = await store.last_sweep(repo)
    runs_listed_from = last.runs_listed_from if last else None
    runs_listed_to = last.runs_listed_to if last else None
    # The existing PR-update cursor is shared with historical queue records;
    # only merge facts, not queue timelines, are read past it now.
    queue_read_to = last.queue_read_to if last else None
    credential = await github_read_token_for_repo(repo, session, background=True)
    if credential.token is None:
        # No token this round. Report the id the current binding resolves to
        # (the quota-closed branch still fills it) or None — never a previous
        # sweep's id, which a re-install or rebind may have made stale.
        report["installation_id"] = credential.installation_id
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
    # The id came back with the credential, read off the binding and token
    # objects already resolved — no extra request, no stored token.
    report["installation_id"] = credential.installation_id
    # The last count stands until this sweep counts again (it may run out of
    # time first).
    report["jobs_owed"] = int(last.report.get("jobs_owed") or 0) if last else 0

    # This sweep's row, once its first unit is committed, and what the last
    # commit held: a dropped unit goes back to it.
    row: CiActivitySweep | None = None
    committed: dict[str, Any] = {
        "cursors": (runs_listed_from, runs_listed_to, queue_read_to),
        "counters": {k: report[k] for k in _COUNTERS},
    }

    async def record(*, finished: bool) -> None:
        nonlocal row
        report["request_counts"] = _request_delta(before, reader.request_counts)
        report["requests"] = report["request_counts"]["http_attempts"]
        row = await store.record_sweep(
            repo,
            row=row,
            started_at=now,
            runs_listed_from=runs_listed_from,
            runs_listed_to=runs_listed_to,
            queue_read_to=queue_read_to,
            # A row the sweep has not finished says so: the page's "last sweep"
            # is the last finished one (`board._sweep_status`).
            report=report if finished else {**report, "in_progress": True},
        )
        committed["cursors"] = (runs_listed_from, runs_listed_to, queue_read_to)
        committed["counters"] = {k: report[k] for k in _COUNTERS}
        if not finished:
            await _bound_transaction(session)

    def complete(listed: Listed, what: str) -> None:
        if listed.truncated:
            report["truncated"].append(what)
            # i18n-exempt: internal admin report about incomplete source coverage
            raise CiGitHubError(f"Truncated {what}; coverage cursor was not advanced")

    async def read_runs(
        since: datetime, until: datetime, *, bulk: bool, oldest_first: bool = True
    ) -> None:
        """List ``[since, until)`` and commit it a leaf at a time, each leaf
        extending the coverage cursors it adjoins: oldest leaf first moving
        ``runs_listed_to`` up, newest first moving ``runs_listed_from`` down. A
        bulk read (a downtime gap, the backfill) has one page per leaf, so no
        unit outlasts one request and every sweep keeps what it read."""
        nonlocal runs_listed_from, runs_listed_to
        async for low, high, listed in reader.run_leaves(
            owner=owner,
            repo=name,
            token=token,
            since=since,
            until=until,
            per_leaf=RUNS_PAGE if bulk else RESULT_CAP,
            oldest_first=oldest_first,
        ):
            complete(listed, f"runs from {low.isoformat()}")
            report["runs_upserted"] += await store.upsert_runs(repo, listed.items)
            covered_to = min(high, now)
            runs_listed_to = (
                max(runs_listed_to, covered_to) if runs_listed_to else covered_to
            )
            if runs_listed_from is None or runs_listed_from > low:
                runs_listed_from = low
            await record(finished=False)

    deadline = asyncio.get_running_loop().time() + SWEEP_DEADLINE.total_seconds()
    try:
        async with asyncio.timeout_at(deadline):
            reader.check_quota(token)
            recent_from = now - RECENT
            oldest_open = await store.oldest_open_run(repo, now - OPEN_LOOKBACK)
            if oldest_open is not None:
                recent_from = min(recent_from, oldest_open)
            cursor = _slice_start(max(window_start, recent_from))
            if runs_listed_to is not None and runs_listed_to < cursor:
                # A downtime gap older than the revalidation range is read once,
                # in bounded bulk slices, rather than hundreds of empty windows.
                missing = _slice_start(max(window_start, runs_listed_to))
                while missing < cursor:
                    until = min(cursor, missing + BACKFILL_SLICE)
                    await read_runs(missing, until, bulk=True)
                    missing = until
            while cursor < now:
                until = cursor + RUN_SLICE
                await read_runs(cursor, until, bulk=False)
                cursor = until

            for _ in range(backfill_slices):
                if runs_listed_from is None or runs_listed_from <= window_start:
                    break
                since = max(window_start, runs_listed_from - BACKFILL_SLICE)
                await read_runs(since, runs_listed_from, bulk=True, oldest_first=False)

            for run in await store.runs_owing_jobs(repo, jobs_budget):
                if run.conclusion == "skipped":
                    await store.replace_jobs(run, [], attempt=run.run_attempt)
                    await record(finished=False)
                    continue
                try:
                    listed = await reader.run_jobs(
                        owner=owner, repo=name, token=token, run_id=run.run_id
                    )
                except CiGitHubError as exc:
                    if exc.status != 404:
                        raise
                    await store.replace_jobs(run, [], attempt=run.run_attempt)
                    report["gone_runs"] += 1
                    await record(finished=False)
                    continue
                complete(listed, f"jobs of run {run.run_id}")
                report["jobs_read"] += await store.replace_jobs(
                    run, listed.items, attempt=run.run_attempt
                )
                await record(finished=False)

            cursor = max(
                window_start, (queue_read_to or window_start) - MERGED_PRS_OVERLAP
            )
            while cursor < now:
                until = min(now, cursor + MERGED_PRS_SLICE)
                listed = await reader.merged_pull_requests(
                    owner=owner, repo=name, token=token, since=cursor, until=until
                )
                complete(listed, f"merged PRs from {cursor.isoformat()}")
                report[
                    "merged_prs_upserted"
                ] += await store.upsert_merged_pull_requests(repo, listed.items)
                queue_read_to = until
                await record(finished=False)
                cursor = until
    except (CiGitHubError, httpx.HTTPError, SQLAlchemyError, TimeoutError) as exc:
        # Only the unit in progress is lost: everything before it is committed.
        await session.rollback()
        runs_listed_from, runs_listed_to, queue_read_to = committed["cursors"]
        report.update(committed["counters"])
        report["error"] = _stopped_by(exc)
        await _bound_transaction(session)

    # Tidying up stays inside the sweep's deadline, held by the server rather
    # than by cancelling a statement mid-flight: each statement gets the time
    # left as its statement timeout, and a sweep with none left skips the rest.
    # The next sweep prunes; until it counts, the owed jobs stay at the last
    # count. Writing the outcome is one short statement under its own timeout.
    loop = asyncio.get_running_loop()
    try:
        for step in ("pruned_runs", "jobs_owed"):
            left = deadline - loop.time()
            if left <= 0:
                raise TimeoutError
            await _limit_statements(session, left)
            report[step] = (
                await store.prune(repo, window_start)
                if step == "pruned_runs"
                else await store.count_runs_owing_jobs(repo)
            )
    except (SQLAlchemyError, TimeoutError) as exc:
        await session.rollback()
        await _bound_transaction(session)
        report["pruned_runs"] = 0
        report["error"] = report["error"] or _stopped_by(exc)
    if runs_listed_from is not None:
        runs_listed_from = max(window_start, runs_listed_from)
    await _limit_statements(session, OUTCOME_DEADLINE.total_seconds())
    await record(finished=True)
    return report
