"""What a task's AI teammate says about where its work stands, and the one
thing the platform adds beside it.

The board used to infer 「运行中」 from open turn intervals, which put a
teammate waiting on a build or a background command back in 「进行中」 the
moment its turn returned. Only the teammate knows whether it is still on the
work, so it says so (`cheese_task_state`), and the board shows exactly what it
said (`presentation.task_presentation`).

Two writers, two columns each, and neither touches the other's:

- the teammate's report: `set_progress` — state, note, when, who;
- the session ending after that report: `note_agent_left` — the board puts
  「已离开」 beside the report. It never rewrites the report: a teammate that
  said 「在等 CI」 and then left is still the last word on that work.
"""

import uuid
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.room_task.models import Task, TaskProgress, TaskStatus

#: The note's width: one line on the task header. The column is this wide.
NOTE_MAX_CHARS = 200


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
    marked = await session.execute(
        update(Task)
        .where(Task.id == conversation_id, Task.status == TaskStatus.open)
        .values(agent_left_at=at)
    )
    # rowcount exists on the CursorResult execute() returns for DML.
    return bool(marked.rowcount)  # type: ignore[attr-defined]
