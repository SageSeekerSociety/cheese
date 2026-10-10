"""A task whose last delivery landed closes once its AI teammate has written it up.

Accepting the last step does not close the task on the spot. The task stays
open, with `closing_since` set, while its AI teammate takes one more turn to
bring the task document up to date and say what came of the task; to everyone
looking it already reads 已采纳 (`presentation`). It closes when that turn ends
(`delivery.agent.run_attempt`), or when the instruction for it gives up, or,
whatever happened to it, `CLOSE_AT_THE_LATEST` after it landed.

Closing after the turn rather than before keeps every rule about closed tasks
as it is: a closed task takes no turns and its document is read-only, and
nothing here needs an exception to either.

Every way a task closes goes through `close_task` — its owner, its own
session, the summary turn ending, the instruction for it given up on, the
deadline above — and so every one of them stops the work still running in the
task once the close commits.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import background
from app.domain.room_task.checkouts import after_close
from app.domain.room_task.models import Task, TaskStatus

#: Half an hour is how long an instruction to an AI teammate is retried
#: (`delivery.agent.GIVE_UP_AFTER`); a summary turn is a few minutes.
CLOSE_AT_THE_LATEST = timedelta(minutes=45)

#: Marks the instruction whose turn ends the task, in the delivery's payload.
CLOSES_TASK = "closesTask"


class StopsWork(Protocol):
    """What stops the work a teammate still has running in a conversation."""

    async def stop_work(self, conversation_id: uuid.UUID) -> None: ...


async def close_task(
    session: AsyncSession,
    task: Task,
    work: StopsWork | None,
    *,
    conclusion: str | None = None,
) -> Task:
    """Close ``task``; with a `conclusion` it is done, without one it was put
    down. Once ``session`` commits, whatever a teammate is still doing in it
    is stopped through ``work``: a closed task is work that is over, and a turn
    left running in it goes on paying for the model while every write it
    makes is refused. ``work`` is None only when the turn that would be
    stopped is the one closing the task.

    Idempotent: closing a closed task keeps the first `closed_at` — the
    moment it stopped being live is a fact, not a re-statement of intent.
    """
    if conclusion is not None:
        task.conclusion = conclusion
    if task.status is not TaskStatus.closed:
        task.status = TaskStatus.closed
        task.closed_at = datetime.now(UTC)
        after_close(session, task.room_id)
        if work is not None:
            _stop_after_commit(session, task.id, work)
    await session.flush()
    return task


def _stop_after_commit(session: AsyncSession, task_id: uuid.UUID, work: StopsWork):
    """Stop ``task_id``'s work once ``session`` commits; a rollback stops
    nothing, since the task it would stop is still open."""
    pending: dict[uuid.UUID, StopsWork] | None = session.info.get("closing_tasks")
    if pending is None:
        pending = session.info["closing_tasks"] = {}

        def committed(_session) -> None:
            for closed, stopper in list(pending.items()):
                background.spawn(
                    stopper.stop_work(closed), name="stop a closed task's work"
                )
            pending.clear()

        def rolled_back(_session, _transaction) -> None:
            pending.clear()

        event.listen(session.sync_session, "after_commit", committed)
        event.listen(session.sync_session, "after_soft_rollback", rolled_back)
    pending[task_id] = work


async def close_after_summary(
    session: AsyncSession, task_id: uuid.UUID, work: StopsWork
) -> Task | None:
    """Close ``task_id`` if it is waiting for its summary turn. Returns the
    task it closed; the pages showing it hear from `room_task.live`."""
    task = await session.get(Task, task_id, with_for_update=True)
    if task is None or task.closing_since is None:
        return None
    task.closing_since = None
    return await close_task(session, task, work)


async def close_overdue(session: AsyncSession, work: StopsWork) -> list[Task]:
    """Close every task still waiting for a summary turn it is not getting."""
    overdue = list(
        await session.scalars(
            select(Task.id).where(
                Task.closing_since.is_not(None),
                Task.closing_since < datetime.now(UTC) - CLOSE_AT_THE_LATEST,
            )
        )
    )
    closed = [await close_after_summary(session, task_id, work) for task_id in overdue]
    return [task for task in closed if task is not None]
