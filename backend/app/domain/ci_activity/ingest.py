"""Bring the CI activity tables up to date without spending people's quota.

New runs are discovered in fixed five-minute creation-time windows. The same
windows are revalidated across the existing three-hour range, extended to old
open runs up to a day back: completion and reruns do not change created_at.
Their stable URLs can earn free 304s instead of re-downloading a rolling list.
Reruns outside that existing range remain outside this collector's coverage.

Historical backfill and the per-run jobs-attempt ledger are preserved. PR merge
facts still advance by update time, without fetching queue timelines. A failed,
truncated or deferred read never advances a cursor beyond an unfinished unit;
previously committed units survive and the next sweep retries the gap. Every
sweep, including a deferred one, records its reason and actual reader counts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.ci_activity.github import CiGitHubError, GitHubCi, Listed
from app.domain.ci_activity.store import CiActivityStore
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


class CiSource(Protocol):
    @property
    def request_counts(self) -> dict[str, Any]: ...

    def check_quota(self, token: str) -> None: ...

    async def list_runs(
        self, *, owner: str, repo: str, token: str, since: datetime, until: datetime
    ) -> Listed: ...

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
    repo = repo or settings.platform_repository
    now = now or datetime.now(UTC)
    jobs_budget = JOBS_PER_SWEEP if jobs_budget is None else jobs_budget
    backfill_slices = (
        BACKFILL_SLICES_PER_SWEEP if backfill_slices is None else backfill_slices
    )
    window_start = now - WINDOW
    reader: CiSource = source or GitHubCi()
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

    def complete(listed: Listed, what: str) -> None:
        if listed.truncated:
            report["truncated"].append(what)
            # i18n-exempt: internal admin report about incomplete source coverage
            raise CiGitHubError(f"Truncated {what}; coverage cursor was not advanced")

    async def read_runs(since: datetime, until: datetime) -> None:
        nonlocal runs_listed_from, runs_listed_to
        listed = await reader.list_runs(
            owner=owner, repo=name, token=token, since=since, until=until
        )
        complete(listed, f"runs from {since.isoformat()}")
        report["runs_upserted"] += await store.upsert_runs(repo, listed.items)
        covered_to = min(until, now)
        runs_listed_to = (
            max(runs_listed_to, covered_to) if runs_listed_to else covered_to
        )
        if runs_listed_from is None or runs_listed_from > since:
            runs_listed_from = since

    try:
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
                await read_runs(missing, until)
                missing = until
        while cursor < now:
            until = cursor + RUN_SLICE
            await read_runs(cursor, until)
            cursor = until

        for _ in range(backfill_slices):
            if runs_listed_from is None or runs_listed_from <= window_start:
                break
            since = max(window_start, runs_listed_from - BACKFILL_SLICE)
            await read_runs(since, runs_listed_from)

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
                await store.replace_jobs(run, [], attempt=run.run_attempt)
                report["gone_runs"] += 1
                continue
            complete(listed, f"jobs of run {run.run_id}")
            report["jobs_read"] += await store.replace_jobs(
                run, listed.items, attempt=run.run_attempt
            )

        cursor = max(window_start, (queue_read_to or window_start) - MERGED_PRS_OVERLAP)
        while cursor < now:
            until = min(now, cursor + MERGED_PRS_SLICE)
            listed = await reader.merged_pull_requests(
                owner=owner, repo=name, token=token, since=cursor, until=until
            )
            complete(listed, f"merged PRs from {cursor.isoformat()}")
            report["merged_prs_upserted"] += await store.upsert_merged_pull_requests(
                repo, listed.items
            )
            cursor = queue_read_to = until
    except (CiGitHubError, httpx.HTTPError, SQLAlchemyError) as exc:
        await session.rollback()
        # i18n-exempt: GitHub's own words, for the admin sweep report
        report["error"] = f"{type(exc).__name__}: {exc}"

    report["request_counts"] = _request_delta(before, reader.request_counts)
    report["requests"] = report["request_counts"]["http_attempts"]
    report["pruned_runs"] = await store.prune(repo, window_start)
    if runs_listed_from is not None:
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
