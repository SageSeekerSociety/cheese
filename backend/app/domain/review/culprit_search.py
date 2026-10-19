"""Running one culprit search, one probe per sweep.

A red batch is searched for the commit that introduced the failure by rerunning
the failed tests on commits in the middle of it (`culprit_bisect`). A probe is
a CI run: a sweep cannot wait for one, so each sweep does at most one step —
read the verdict of the probe that is out, or dispatch the next one — and the
plan is stored between steps (`culprit_models`).

The search answers, or it does not: a probe that cannot judge, a budget spent,
a probe that never finishes, and a dispatch the forge refuses all end it
without naming a commit, and the reason is carried to the batch's notice. It
never picks a commit to have an answer.
"""

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionFactory
from app.domain.review import culprit_bisect
from app.domain.review.culprit_bisect import Search
from app.domain.review.culprit_models import CulpritSearch
from app.domain.review.landing_forge import LandingReads

logger = logging.getLogger("cheesex.review.culprit_search")

#: How long one probe may stay out. The workflow's own timeout is 20 minutes;
#: past this the run is not coming back, and waiting would keep the batch's
#: red unreported.
PROBE_FOR = timedelta(minutes=25)
#: How long the whole search may take. `MAX_PROBES` runs at `PROBE_FOR` could
#: outlast the landing watch, and a watch that expires says nothing at all.
SEARCH_FOR = timedelta(minutes=50)
#: A finished search is read by every landing of the batch; after this nobody
#: will ask again (the watch is two hours) and the row can go.
KEEP_FOR = timedelta(days=2)


@dataclass(frozen=True, slots=True)
class Finding:
    """What one batch's culprit search comes to this sweep.

    ``unavailable`` is not a failed search: the platform could not run a probe
    at all (no such workflow, no permission), so the batch is attributed the
    way it was before the search existed. ``unlocated`` is a search that ran
    and could not name a commit, and the reason is what the notice reports.
    """

    state: Literal["searching", "located", "unlocated", "unavailable"]
    culprit: str | None = None
    reason: str = ""


async def advance(
    sessions: SessionFactory,
    reads: LandingReads,
    *,
    project_id: uuid.UUID,
    head: str,
    base: str | None,
    commits: list[str],
    tests: list[str],
    now: datetime | None = None,
) -> Finding:
    """Take the batch's search one step: read a verdict, or dispatch a probe."""
    now = now or datetime.now(UTC)
    async with sessions() as session:
        row = await _row(session, project_id, head)
        if row is None:
            if len(commits) < culprit_bisect.MIN_COMMITS or not tests:
                return Finding("unavailable")
            row = CulpritSearch(
                project_id=project_id,
                head=head,
                tests=list(tests),
                state=_dump(culprit_bisect.begin(commits, base)),
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # Another sweep created it first; its step is the one that counts.
                await session.rollback()
                row = await _row(session, project_id, head)
                if row is None:
                    return Finding("searching")
        search = _load(row.state)
        run_id, since = row.run_id, row.dispatched_at
        started = row.created_at

    if search.finished:
        return _finding(search)
    if now - started >= SEARCH_FOR:
        search = replace(search, pending="", stopped="the search ran out of time")
        await _save(sessions, project_id, head, search)
        return _finding(search)
    if run_id is not None and search.pending:
        if since is not None and now - since >= PROBE_FOR:
            search = replace(
                search, pending="", at_base=False, stopped="the probe did not finish"
            )
            await _save(sessions, project_id, head, search)
            return _finding(search)
        verdict = await reads.bisect_verdict(run_id)
        if verdict == "running":
            return Finding("searching")
        search = search.advance(verdict)
        await _save(sessions, project_id, head, search)
        return _finding(search)

    sha = search.probe
    if not sha:
        return _finding(search)
    search = search.waiting_for(sha)
    run = await reads.dispatch_bisect(sha, list(tests), uuid.uuid4().hex[:12])
    if run is None:
        if not search.probed:
            # Nothing was learned: fall back to how such a batch was attributed
            # before the search existed rather than call it unlocatable. The
            # row goes, so a forge that cannot probe is not left holding one.
            await _delete(sessions, project_id, head)
            return Finding("unavailable", reason="a probe could not be started")
        search = replace(
            search, pending="", at_base=False, stopped="a probe could not be started"
        )
        await _save(sessions, project_id, head, search)
        return _finding(search)
    await _save(sessions, project_id, head, search, run_id=run, dispatched_at=now)
    return Finding("searching")


async def purge(sessions: SessionFactory, *, now: datetime | None = None) -> int:
    """Drop searches nobody will read again."""
    now = now or datetime.now(UTC)
    async with sessions() as session:
        result = await session.execute(
            delete(CulpritSearch).where(CulpritSearch.updated_at < now - KEEP_FOR)
        )
        await session.commit()
    return result.rowcount or 0


def _finding(search: Search) -> Finding:
    if not search.finished:
        return Finding("searching")
    if search.culprit is not None:
        return Finding("located", culprit=search.culprit)
    return Finding("unlocated", reason=search.reason)


async def _row(session, project_id: uuid.UUID, head: str) -> CulpritSearch | None:
    return await session.scalar(
        select(CulpritSearch).where(
            CulpritSearch.project_id == project_id,
            CulpritSearch.head == head,
        )
    )


async def _delete(sessions: SessionFactory, project_id: uuid.UUID, head: str) -> None:
    async with sessions() as session:
        await session.execute(
            delete(CulpritSearch).where(
                CulpritSearch.project_id == project_id,
                CulpritSearch.head == head,
            )
        )
        await session.commit()


async def _save(
    sessions: SessionFactory,
    project_id: uuid.UUID,
    head: str,
    search: Search,
    *,
    run_id: str | None = None,
    dispatched_at: datetime | None = None,
) -> None:
    async with sessions() as session:
        row = await _row(session, project_id, head)
        if row is None:
            return
        row.state = _dump(search)
        row.run_id = run_id
        row.dispatched_at = dispatched_at
        await session.commit()


def _dump(search: Search) -> dict:
    return {
        "commits": list(search.commits),
        "base": search.base,
        "lo": search.lo,
        "hi": search.hi,
        "probed": list(search.probed),
        "pending": search.pending,
        "at_base": search.at_base,
        "stopped": search.stopped,
    }


def _load(state: dict) -> Search:
    return Search(
        commits=tuple(state.get("commits") or ()),
        base=state.get("base"),
        lo=int(state.get("lo") or 0),
        hi=int(state.get("hi") or 0),
        probed=tuple(state.get("probed") or ()),
        pending=state.get("pending") or "",
        at_base=bool(state.get("at_base")),
        stopped=state.get("stopped") or "",
    )
