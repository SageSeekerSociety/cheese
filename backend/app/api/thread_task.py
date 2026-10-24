"""A 支线 becoming the task it has turned into, where it is.

Two roads lead here and they do the same thing: a person turns the 支线
(`POST /blocks/{block}/upgrade` on the message it hangs under, which the 支线's
「转为任务」 is), or its AI teammate does on someone's word (`cheese_task(action=open)` with
``here``). The task takes the 支线's id, so the conversation, its AI teammate's
session and everything said in it carry on; from the next turn the session
keeps its work (`Place.keeps_work`).
"""

import uuid

from sqlalchemy import select

from app.api.task_instructions import say_became_task
from app.domain.agent_instance.adapters import project_team, seat_project
from app.domain.block.models import Block, BlockKind
from app.domain.identity.handles import recipient_seat
from app.domain.room_task.dispatch import create_task
from app.domain.room_task.models import Task
from app.domain.thread.models import Thread
from app.domain.thread.services import thread_teammate


def turns_in_place(block: Block, room_id: uuid.UUID) -> bool:
    """Whether turning ``block`` into a task turns its 支线 into the task: a
    message said in its channel's main line. A file sent there, a reply in a
    支线 or a message in a task becomes a task of its own instead."""
    return block.conversation_id == room_id and block.kind == BlockKind.message


async def turn_into_task(
    db,
    thread: Thread,
    *,
    owner: str | None,
    by: str,
    title: str | None = None,
    named_by_teammate: bool = False,
    source_block_id: uuid.UUID | None = None,
) -> tuple[Task, bool]:
    """``thread`` as a task owned by ``owner``, and whether it became one just
    now. Turning a 支线 that already is one gives that task back unchanged:
    two people pressing at once make one task.

    Unnamed (``title`` None), the platform names it from the conversation, as
    any task made without a title (`room_task/naming.py`)."""
    locked = await db.scalar(
        select(Thread).where(Thread.id == thread.id).with_for_update()
    )
    assert locked is not None
    existing = await db.get(Task, thread.id)
    if existing is not None:
        return existing, False
    # The teammate the 支线 was talking with goes on working it: its session is
    # keyed by the conversation and that teammate, so another one would start a
    # new conversation instead of carrying this one on. Nobody has answered in
    # a 支线 just opened: the teammate its message called takes it up, seated
    # in the channel if it is not (`TopicService.create_task`).
    teammate = await thread_teammate(db, thread)
    if teammate is None:
        root = await db.get(Block, thread.root_block_id)
        teammate = (
            recipient_seat((root.meta or {}).get("agent_recipient"))
            if root is not None
            else None
        )
    task = await create_task(
        db,
        room_id=thread.room_id,
        created_by=by,
        title=title,
        owner_handle=owner,
        teammate=teammate,
        named_by_teammate=named_by_teammate,
        origin_block_id=thread.root_block_id,
        source_block_id=source_block_id,
        thread_id=thread.id,
        seat_project=seat_project,
        team=project_team,
    )
    await say_became_task(db, task)
    return task, True
