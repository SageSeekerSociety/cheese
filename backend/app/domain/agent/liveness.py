"""Which tasks have a turn running right now — the board's 「运行中」.

Two places know, and either is enough to say a turn started. The process
running the turn knows at once (``ChatService.live.active_turn_ids``), but only for
its own turns and only until it is replaced. The turn's interval knows across
processes: it opens when the prompt reaches the task's session and closes when
the session stops, or when the orphan sweep finds it dead. So a task is running
when an interval of one is open and was delivered, or this process is running
a turn in it whose interval has not closed.

Only the interval says a turn ended. This process lets go of a turn a moment
after its interval closes, and the pages are told the turn ended when the close
commits (`room_task.live.turns_moved`); a read in that moment must already say
the turn is over, or the page keeps 运行中 with nothing left to tell it again.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.models import AgentTurn
from app.domain.room_task.models import Task


async def running_tasks(chat, db: AsyncSession, tasks: list[Task]) -> set[uuid.UUID]:
    """The ids of ``tasks`` with a turn running in their own conversation."""
    ids = [task.id for task in tasks]
    if not ids:
        return set()
    here = {task_id: set(chat.live.active_turn_ids.get(task_id, ())) for task_id in ids}
    turns_here = set().union(*here.values())
    ended = (
        set(
            await db.scalars(
                select(AgentTurn.id).where(
                    AgentTurn.id.in_(turns_here), AgentTurn.stopped_at.is_not(None)
                )
            )
        )
        if turns_here
        else set()
    )
    open_turns = await db.scalars(
        select(AgentTurn.conversation_id).where(
            AgentTurn.conversation_id.in_(ids),
            AgentTurn.stopped_at.is_(None),
            AgentTurn.delivered_at.is_not(None),
        )
    )
    return {task_id for task_id, turns in here.items() if turns - ended} | {
        task_id for task_id in open_turns if task_id is not None
    }
