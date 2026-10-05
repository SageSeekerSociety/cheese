"""Which tasks have a turn running right now — the board's 「运行中」.

Two places know, and either is enough. The process running the turn knows at
once (``ChatService.has_running_turn``), but only for its own turns and only
until it is replaced. The turn's interval knows across processes: it opens when
the prompt reaches the task's session and closes when the session stops, or
when the orphan sweep finds it dead. So a task is running when this process is
running a turn in it, or an interval of one is open and was delivered.
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
    here = {task_id for task_id in ids if chat.has_running_turn(task_id)}
    open_turns = await db.scalars(
        select(AgentTurn.task_id).where(
            AgentTurn.task_id.in_(ids),
            AgentTurn.stopped_at.is_(None),
            AgentTurn.delivered_at.is_not(None),
        )
    )
    return here | {task_id for task_id in open_turns if task_id is not None}
