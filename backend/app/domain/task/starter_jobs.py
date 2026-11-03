"""Pushing a 题目's starter code into the repositories of the projects claimed
from it (#4767 §二), after the claim has committed.

A claim records which archive it took (`ProjectStarterPush`) and returns; this
job unpacks that archive and pushes it as the repository's first commit. The
claim therefore never holds its transaction — or the 题目's claim lock — while
a large archive is fetched, unpacked and pushed, and many students can claim
at once.

One row at a time, leased the way the notification outbox leases a delivery:
the lease is taken and committed before the work starts, no transaction is held
while the forge is contacted, and a worker whose lease was taken over cannot
record its result. ``done`` means the starter commit is on `main`. A push
that can never land fails at once with the sentence that says why: the archive
is refused when read, the publisher removed it, or `main` was made by someone
else first. One that hit a passing condition (the forge did not answer) is
tried again later, and after ``MAX_ATTEMPTS`` it fails too, saying so.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select, update

from app.core.db import SessionFactory
from app.core.errors import BaseError, UnprocessableEntityError, message_key
from app.core.sentences import say
from app.domain.library.models import LibraryFileRecord
from app.domain.project import forge
from app.domain.project import starter as starter_archive
from app.domain.project.models import Project, ProjectStarterPush, StarterPushState
from app.domain.task import package

log = logging.getLogger(__name__)

#: Longer than any one push can run (every git step has a 600 s timeout).
LEASE = timedelta(minutes=60)
MAX_ATTEMPTS = 8
#: How long a passing failure waits before the next try, doubling, capped.
FIRST_RETRY = timedelta(seconds=30)
LONGEST_RETRY = timedelta(minutes=30)


def _sentence(exc: Exception) -> dict:
    found = message_key(getattr(exc, "message", None))
    return found or say("forgejoStarterPushFailed").descriptor()


async def _lease(sessions: SessionFactory) -> tuple[uuid.UUID, uuid.UUID] | None:
    stamp = datetime.now(UTC)
    async with sessions() as session:
        row = await session.scalar(
            select(ProjectStarterPush)
            .where(
                ProjectStarterPush.state == StarterPushState.pending,
                or_(
                    ProjectStarterPush.lease_until.is_(None),
                    ProjectStarterPush.lease_until <= stamp,
                ),
                or_(
                    ProjectStarterPush.retry_at.is_(None),
                    ProjectStarterPush.retry_at <= stamp,
                ),
            )
            .order_by(ProjectStarterPush.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        row.attempts += 1
        token = uuid.uuid4()
        row.claim_token = token
        row.lease_until = stamp + LEASE
        leased = (row.project_id, token)
        await session.commit()
    return leased


async def _push(
    sessions: SessionFactory, project_id: uuid.UUID, token: uuid.UUID
) -> None:
    """Put the starter commit on the repository's `main`. Raises what went wrong.

    The repository is asked first what already happened, before the archive is
    looked at: an earlier attempt may have pushed and lost its answer (then the
    commit it recorded is on `main`, whatever became of the archive since), or
    someone with write access may have made `main` before the push (then the
    starter commit is not on it, and that is said, not called done)."""
    async with sessions() as session:
        push = await session.get(ProjectStarterPush, project_id)
        assert push is not None  # leased just now
        recorded = push.commit_sha
        message, when, file_id = push.message, push.created_at, push.library_file_id
        remote = await forge.starter_remote(project_id, session)
    main = await asyncio.to_thread(starter_archive.remote_main, remote)
    if main is not None:
        if recorded is not None and await asyncio.to_thread(
            starter_archive.holds, remote, main, recorded
        ):
            return
        raise UnprocessableEntityError(say("starterMainTaken"))
    async with sessions() as session:
        row = await session.get(LibraryFileRecord, file_id) if file_id else None
        if row is None:
            raise UnprocessableEntityError(say("starterRemovedBeforePush"))
        session.expunge(row)
    # No transaction is held while the archive is fetched, built and pushed.
    with tempfile.TemporaryDirectory(prefix="starter-") as temporary:
        archive = Path(temporary) / "starter.zip"
        with archive.open("wb") as out:
            await package.write_file(row, out)
        repository = Path(temporary) / "starter.git"
        commit = await asyncio.to_thread(
            starter_archive.build, archive, message, when, repository
        )
        # Recorded before it is pushed: a push whose answer is lost is then
        # recognised on the next attempt by this commit being on `main`.
        await _note_commit(sessions, project_id, token, commit)
        await asyncio.to_thread(starter_archive.push, remote, repository, commit)


async def _note_commit(
    sessions: SessionFactory, project_id: uuid.UUID, token: uuid.UUID, commit: str
) -> None:
    async with sessions() as session:
        await session.execute(
            update(ProjectStarterPush)
            .where(
                ProjectStarterPush.project_id == project_id,
                ProjectStarterPush.claim_token == token,
            )
            .values(commit_sha=commit)
        )
        await session.commit()


async def _record(
    sessions: SessionFactory,
    project_id: uuid.UUID,
    token: uuid.UUID,
    failure: Exception | None,
) -> str:
    async with sessions() as session:
        push = await session.scalar(
            select(ProjectStarterPush)
            .where(
                ProjectStarterPush.project_id == project_id,
                ProjectStarterPush.claim_token == token,
                ProjectStarterPush.state == StarterPushState.pending,
            )
            .with_for_update()
        )
        if push is None:
            return "lost"
        push.lease_until = None
        now = datetime.now(UTC)
        if failure is None:
            push.state = StarterPushState.done
            push.error = None
            push.finished_at = now
            outcome = "done"
        elif isinstance(failure, UnprocessableEntityError):
            # The archive itself cannot become a commit: no retry changes that.
            push.state = StarterPushState.failed
            push.error = _sentence(failure)
            push.finished_at = now
            outcome = "failed"
        elif push.attempts >= MAX_ATTEMPTS:
            push.state = StarterPushState.failed
            push.error = say("starterPushGaveUp").descriptor()
            push.finished_at = now
            outcome = "failed"
        else:
            push.error = _sentence(failure)
            push.retry_at = now + min(
                FIRST_RETRY * 2 ** (push.attempts - 1), LONGEST_RETRY
            )
            outcome = "retried"
        await session.commit()
    return outcome


#: Told once a push is settled (done or failed), with the project and its
#: root room, so the pages showing the project read it again.
Settled = Callable[[uuid.UUID, uuid.UUID], Awaitable[None]]


async def push_due(
    sessions: SessionFactory, *, settled: Settled | None = None, batch: int = 4
) -> dict[str, int]:
    """Push up to ``batch`` starter archives whose turn has come."""
    counts = {"done": 0, "failed": 0, "retried": 0, "lost": 0}
    for _ in range(batch):
        leased = await _lease(sessions)
        if leased is None:
            break
        project_id, token = leased
        failure: Exception | None = None
        try:
            await _push(sessions, project_id, token)
        except BaseError as exc:
            failure = exc
        except Exception as exc:  # a crash is a retry, and logged
            log.exception("starter push for project %s failed", project_id)
            failure = exc
        outcome = await _record(sessions, project_id, token, failure)
        counts[outcome] += 1
        if settled is not None and outcome in ("done", "failed"):
            async with sessions() as session:
                room = await session.scalar(
                    select(Project.root_topic_id).where(Project.id == project_id)
                )
            if room is not None:
                await settled(project_id, room)
    return counts
