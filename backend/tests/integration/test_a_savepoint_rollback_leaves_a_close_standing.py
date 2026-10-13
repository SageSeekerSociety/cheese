"""A savepoint that rolls itself back takes no close with it.

`close_task` stages the stop of a closed task's work on the session's commit
(`room_task.closing._stop_after_commit`). The same request then says something
it can do without: `review.task_landing.tell_origin` wraps the courtesy line in
`begin_nested()`, and a line that cannot be said rolls that savepoint back.
Rolling a savepoint back undoes none of the close — the task row is still
closed and the request goes on to commit — so the stop has to survive it.

What reaches `after_soft_rollback` is not always the session's own rollback,
and the two mean the opposite thing: the session's own rollback says the close
never happened, and the stop is dropped; a savepoint that rolls back on its own
says only that the courtesy line was not said. Without that guard `pending` is
emptied by the failed line, the commit stops nothing, and the closed task's
teammate goes on working — and being paid for — until the periodic sweep
notices. The stop is pinned here rather than through `close_task` because what
the guard decides is which rollbacks undo a close, and the end-to-end path
needs a turn actually running to observe it.

`test_a_released_savepoint_is_not_a_commit.py` pins the other side of these
events: that a released savepoint is not a commit.
"""

import asyncio
import uuid

from app.domain.room_task import closing


class Stopped:
    """A `StopsWork` that writes down what it was asked to stop."""

    def __init__(self) -> None:
        self.conversations: list[uuid.UUID] = []

    async def stop_work(self, conversation_id: uuid.UUID) -> None:
        self.conversations.append(conversation_id)


def _close_saying_a_line_that_fails(
    db_session, _portal
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Stage a close, roll a savepoint back alone, then commit.

    Answers the task that was closed and what the commit stopped.
    """
    work = Stopped()
    task_id = uuid.uuid4()

    async def scenario() -> list[uuid.UUID]:
        closing._stop_after_commit(db_session, task_id, work)
        try:
            async with db_session.begin_nested():
                raise RuntimeError("the line could not be said")
        except RuntimeError:
            pass
        assert work.conversations == [], "the stop waits for the commit"
        await db_session.commit()
        for _ in range(20):  # the stop is spawned, not awaited here
            await asyncio.sleep(0)
        return list(work.conversations)

    return task_id, _portal.call(scenario)


def test_a_savepoint_that_rolls_itself_back_leaves_the_close_standing(
    db_session, _portal
):
    task_id, stopped = _close_saying_a_line_that_fails(db_session, _portal)

    assert stopped == [task_id]
