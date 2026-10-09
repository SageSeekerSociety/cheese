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
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.room_task.checkouts import after_close
from app.domain.room_task.models import Task, TaskStatus

#: Half an hour is how long an instruction to an AI teammate is retried
#: (`delivery.agent.GIVE_UP_AFTER`); a summary turn is a few minutes.
CLOSE_AT_THE_LATEST = timedelta(minutes=45)

#: Marks the instruction whose turn ends the task, in the delivery's payload.
CLOSES_TASK = "closesTask"


async def close_after_summary(session: AsyncSession, task_id: uuid.UUID) -> Task | None:
    """Close ``task_id`` if it is waiting for its summary turn. Returns the
    task it closed; the caller tells the pages showing it."""
    task = await session.get(Task, task_id, with_for_update=True)
    if task is None or task.closing_since is None:
        return None
    task.closing_since = None
    if task.status == TaskStatus.open:
        task.status = TaskStatus.closed
        task.closed_at = datetime.now(UTC)
        after_close(session, task.room_id)
    await session.flush()
    return task


async def close_overdue(session: AsyncSession) -> list[Task]:
    """Close every task still waiting for a summary turn it is not getting."""
    overdue = list(
        await session.scalars(
            select(Task.id).where(
                Task.closing_since.is_not(None),
                Task.closing_since < datetime.now(UTC) - CLOSE_AT_THE_LATEST,
            )
        )
    )
    closed = [await close_after_summary(session, task_id) for task_id in overdue]
    return [task for task in closed if task is not None]
