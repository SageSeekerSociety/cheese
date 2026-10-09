"""A task changed: every page showing it reads it again, whichever path changed it.

A task is on two pages at once: its own, which listens on the task's
conversation, and its channel's, where its card hangs under a message and in
the 支线 pane. Telling them used to be each writer's job, and each told the page
it thought of: the summary turn closing the task after 「采纳并完成任务」 was
heard in the channel and not on the task's page, which kept its input box until
a reload; a question asked in a new task was heard by neither, so its card in
the channel said 讨论中 while the task said 待回答.

So the telling happens where the facts are written, like an accept card's
(`review/live.py`). Once a session commits, the task's channel and its own
conversation are told when it flushed

- a change to the task's row (started, closed, accepted, renamed, handed over…);
- a question asked in the task, or an answer recorded on one: what the task
  reads as (`presentation`, 待回答) turns on its latest question;
- a turn in the task reaching its session or ending (运行中): the turn's
  interval is written with a bulk `update(AgentTurn)`, which a flush hook does
  not see, so its writer (`AgentTurnRepository`) queues the frames through
  `turns_moved` in the same session.

Both frames name the ROOM: the sidebar's row is the room's, and a task id is not
an address (`GET /topics/{task}` answers 404). A rollback tells nobody. A bulk
`update(Task)` is not seen here; the one that renames a task tells the pages
itself (`naming._publish`).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.live_frames import SHOW_ONCE_COMMITTED
from app.domain.block.models import Block, BlockKind
from app.domain.room_task.models import Task

#: Rewritten by bookkeeping that no page shows: a change to only these is not
#: worth a reread on every open page.
_UNSHOWN = frozenset(
    {"updated_at", "title_checked_at", "title_calibrated", "last_check_at"}
)


def _changed_on_screen(task: Task) -> bool:
    return any(
        attr.history.has_changes() and attr.key not in _UNSHOWN
        for attr in inspect(task).attrs
    )


def _a_question_moved(session: Session, block: Block) -> bool:
    """A question was asked, or an answer was recorded on it."""
    if block.kind != BlockKind.message or not (block.meta or {}).get("options"):
        return False
    return block in session.new or inspect(block).attrs.meta.history.has_changes()


def _tell(
    session: Session | AsyncSession, task_id: uuid.UUID, room_id: uuid.UUID
) -> None:
    frame = {"type": "state", "resource": "topics", "id": str(room_id)}
    queued = session.info.setdefault(SHOW_ONCE_COMMITTED, [])
    for channel in (str(room_id), str(task_id)):
        if (channel, frame) not in queued:
            queued.append((channel, frame))


@event.listens_for(Session, "after_flush")
def _note_tasks(session: Session, _context) -> None:
    # `new` / `dirty` still describe what this flush wrote while it runs.
    for row in session.new:
        if isinstance(row, Task):
            _tell(session, row.id, row.room_id)
    for row in session.dirty:
        if isinstance(row, Task) and _changed_on_screen(row):
            _tell(session, row.id, row.room_id)
    asked = {
        row.conversation_id
        for row in (*session.new, *session.dirty)
        if isinstance(row, Block) and _a_question_moved(session, row)
    }
    if asked:
        # On the flush's own connection: no ORM query (it would autoflush
        # mid-flush), and nothing left running after the session is done.
        rooms = session.connection().execute(
            select(Task.id, Task.room_id).where(Task.id.in_(asked))
        )
        for task_id, room_id in rooms:
            _tell(session, task_id, room_id)


async def turns_moved(
    session: AsyncSession, conversations: Iterable[uuid.UUID]
) -> None:
    """A turn reached its session, or ended, in these conversations: the ones
    that are tasks are told once ``session`` commits. Read in ``session``
    itself, so nothing is left running once its caller is done with it."""
    ids = set(conversations)
    if not ids:
        return
    rows = await session.execute(select(Task.id, Task.room_id).where(Task.id.in_(ids)))
    for task_id, room_id in rows:
        _tell(session, task_id, room_id)
