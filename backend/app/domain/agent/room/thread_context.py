"""What a session in a channel is told about where it is: the channel's main
line, or one of its 支线 (``turn/intake/preparation/assembly.py``)."""

from typing import TYPE_CHECKING

from app.domain.block.authorship import speaker
from app.domain.prompts import piece
from app.domain.room_task.models import TaskStatus
from app.domain.thread.reads import routine_runs_of, said_before
from app.domain.topic.models import Topic

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: How many of the main line's messages a session in the channel opens with:
#: what was being talked about before what it is handed.
THREAD_CONTEXT_MESSAGES = 10


def _line(block) -> str:
    return f"[id={block.id}] [{speaker(block)}] {block.content}"


async def channel_context(session: "AsyncSession", room: Topic, first) -> str:
    """What a session in a channel's main line is told about where it is: the
    channel, what it may do there, and what the main line said just before
    ``first``, the oldest message it is handed. The channel's tasks are a
    section of their own (`channel_tasks`)."""
    parts = [piece("context/channel", title=room.title)]
    earlier = (
        await said_before(session, first, limit=THREAD_CONTEXT_MESSAGES)
        if first is not None
        else []
    )
    if earlier:
        parts += ["", "在这之前，主线上说的是：", *map(_line, earlier)]
    return "\n".join(parts)


async def thread_context(
    session: "AsyncSession", room: Topic, root, *, routine_run: bool = False
) -> str:
    """What a 支线's session is told about where it is: the channel, the
    message the 支线 hangs under and what the main line said just before it.
    A routine's run is told that this turn may keep what it produces, and that
    the 支线 keeps nothing afterwards. The channel's tasks are a section of
    their own (`channel_tasks`)."""
    run = (await routine_runs_of(session, [root.id])).get(root.id)
    earlier = await said_before(session, root, limit=THREAD_CONTEXT_MESSAGES)
    if run:
        parts = [
            piece(
                "context/routine_run", title=room.title, routine=run.get("title", "")
            ),
            piece(
                "context/routine_run_now"
                if routine_run
                else "context/routine_run_after"
            ),
        ]
    else:
        parts = [
            piece("context/thread", title=room.title),
            "",
            "支线挂在主线的这条消息下面：",
            _line(root),
        ]
    if earlier:
        parts += ["", "这条消息之前，主线上说的是：", *map(_line, earlier)]
    return "\n".join(parts)


async def channel_tasks(session: "AsyncSession", room: Topic, root) -> str:
    """The channel's tasks still open, as a session in its main line or in one
    of its 支线 reads them, so work that already has a task is pointed to
    rather than made into a second one. In a 支线 (``root`` is the message it
    hangs under) the ones made from that message are marked: they are this
    conversation's own.

    A section of the session opening that is told again when it changes
    (`TRACKED_SECTIONS`). A task made while the session was already open — a
    person turning the message into a task after the teammate first answered —
    otherwise never reached it, and the teammate, asked to do the work, made a
    second task for the same thing."""
    # deferred-import: tests replace this name on app.domain.room_task.services
    from app.domain.room_task.services import TaskService

    open_tasks = [
        task
        for task in await TaskService(session).list_in_room(room.id)
        if task.status == TaskStatus.open
    ]
    if not open_tasks:
        return piece("context/tasks_none")
    marks = piece("context/tasks_marks") if root is not None else ""
    return piece("context/tasks_head") + "\n".join(
        [
            piece("context/tasks_rule") + marks,
            *(
                f"- {task.title}"
                + (f"（负责人 @{task.owner_handle}）" if task.owner_handle else "")
                + ("（从这条支线的消息建的）" if _made_from(task, root) else "")
                for task in open_tasks
            ),
        ]
    )


def _made_from(task, root) -> bool:
    return root is not None and task.upgraded_from_block_id == root.id
