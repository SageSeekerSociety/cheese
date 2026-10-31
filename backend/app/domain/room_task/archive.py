"""A room going away takes its tasks with it.

A task with no room has no context and no way back, so closing a place closes
what is inside it. The write is here, next to the rows it changes; the words
that say why are the room's (`topic/services.py` `_close_tasks_of`), because
they land in the room's main line and this package never writes a block.

`topic` sits at layer 2 and this package at layer 4, so it reaches this through
`topic.ports.CloseRoomTasks` — the caller hands this function in. The same
shape `review.archive.close_cards_for_archived_topic` has for the cards of an
archived room.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.output_effects import hold_numbering_now
from app.domain.room_task.lock_order import lock_tasks_for_change
from app.domain.room_task.models import TaskStatus
from app.domain.room_task.services import TaskService


async def close_room_tasks(
    session: AsyncSession, room_ids: Sequence[uuid.UUID]
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """``app.domain.topic.ports.CloseRoomTasks``：把这几间房还没结束的活结束掉，
    还回被结束的那几条（活的 id，它所在的房间）。

    已经有结论的活（`TaskStatus.closed`）不动，也不回报：房间关掉不会把一条早
    就结束的活再结束一次，而 `closed_at` 是它结束的那一刻，不是房间关掉的那一刻。

    Every room the transaction archives, in one call, before anything else is
    locked (`lock_order`): every task's conversation numbering at once, the
    closed ones' too, since archiving goes on to settle the rooms' cards and
    say so in their tasks; then the open ones' rows.
    """
    listed = [
        task
        for room_id in room_ids
        for task in await TaskService(session).list_in_room(room_id)
    ]
    await session.run_sync(hold_numbering_now, [task.id for task in listed])
    closed: list[tuple[uuid.UUID, uuid.UUID]] = []
    for task in await lock_tasks_for_change(
        session, [row.id for row in listed if row.status != TaskStatus.closed]
    ):
        if task.status == TaskStatus.closed:
            continue
        task.status = TaskStatus.closed
        task.closed_at = datetime.now(UTC)
        closed.append((task.id, task.room_id))
    return closed
