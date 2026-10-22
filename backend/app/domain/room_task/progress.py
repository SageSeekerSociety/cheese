"""What a task's AI teammate says about where its work stands, and the one
thing the platform adds beside it.

The board used to infer 「运行中」 from open turn intervals, which put a
teammate waiting on a build or a background command back in 「进行中」 the
moment its turn returned. Only the teammate knows whether it is still on the
work, so it says so (`cheese_task_state`), and the board shows exactly what it
said (`presentation.task_presentation`).

Two writers, two columns each, and neither touches the other's:

- the teammate's report: `set_progress` — state, note, when, who. Only while
  no card waits on the task's review (`PUT /topics/{task}/task/progress`
  refuses it then): the card says where the task stands. The platform's own
  actions write the same columns: a card filed or the task reopened clears the
  report (`clear`), a card returned or voided puts the task back to work
  (`back_to_work`). Each of those tells the teammate (`heard`); filing is its
  own act, and its tool's reply says so;
- the session ending after that report: `note_agent_left` — the board puts
  「已离开」 beside the report, until a session runs in the task again
  (`note_agent_back`). It never rewrites the report: a teammate that said
  「在等 CI」 and then left is still the last word on that work. The teammate
  is not told: the mark is about its own session being gone, and the session
  coming back is what takes it away, so by the time it could read a word of it
  the mark is no longer there.
"""

import uuid
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.room_task.live import rows_written
from app.domain.room_task.models import Task, TaskProgress, TaskStatus

#: The note's width: one line on the task header. The column is this wide.
NOTE_MAX_CHARS = 200


def clear(task: Task) -> None:
    """What the teammate reported is over: a card was filed (the card says
    where the task stands now, 等审), or a person reopened the task (开启)."""
    task.progress_state = None
    task.progress_note = None
    task.progress_set_at = None
    task.progress_set_by = None


def back_to_work(task: Task, *, at: datetime) -> None:
    """The card was returned or voided: the task is back in its teammate's
    hands (在做). Nobody reported it, so nobody is named as having said so
    (``progress_set_by`` stays empty); 「已离开」 stays as it was."""
    task.progress_state = TaskProgress.working
    task.progress_note = None
    task.progress_set_at = at
    task.progress_set_by = None


def set_progress(
    task: Task,
    state: TaskProgress,
    *,
    note: str | None,
    by: str,
    at: datetime,
) -> None:
    """Record the teammate's report. Reporting again means it is back, so the
    「已离开」 beside an older report goes."""
    task.progress_state = state
    task.progress_note = (note or "").strip() or None
    task.progress_set_at = at
    task.progress_set_by = by
    task.agent_left_at = None


async def note_agent_left(
    session: AsyncSession, conversation_id: uuid.UUID, *, at: datetime
) -> bool:
    """The session doing this task ended: mark it beside the teammate's last
    report, leaving the report as it was. ``conversation_id`` is the task's own
    conversation, whose id is the task's. An open task only — a closed one
    says how it ended. Says whether anything was marked; a conversation that is
    not a task marks nothing."""
    room_id = await session.scalar(
        update(Task)
        .where(Task.id == conversation_id, Task.status == TaskStatus.open)
        .values(agent_left_at=at)
        .returning(Task.room_id)
    )
    if room_id is None:
        return False
    rows_written(session, conversation_id, room_id)
    return True


async def note_agent_back(session: AsyncSession, conversation_id: uuid.UUID) -> bool:
    """A session is running in this task again: the 「已离开」 beside the
    teammate's last report goes, and the report stays. Says whether there was
    one to take away."""
    room_id = await session.scalar(
        update(Task)
        .where(
            Task.id == conversation_id,
            Task.status == TaskStatus.open,
            Task.agent_left_at.is_not(None),
        )
        .values(agent_left_at=None)
        .returning(Task.room_id)
    )
    if room_id is None:
        return False
    rows_written(session, conversation_id, room_id)
    return True
