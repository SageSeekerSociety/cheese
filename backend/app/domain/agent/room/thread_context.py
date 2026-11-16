"""What a session in a channel is told about where it is: the channel's main
line, or one of its 支线 (``turn/intake/preparation/assembly.py``)."""

from typing import TYPE_CHECKING

from app.domain.block.authorship import speaker
from app.domain.room_task.models import TaskStatus
from app.domain.thread.reads import routine_runs_of, said_before
from app.domain.topic.models import Topic

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: How many of the main line's messages a session in the channel opens with:
#: what was being talked about before what it is handed.
THREAD_CONTEXT_MESSAGES = 10

#: What a session in a channel's main line or in a 支线 may do, said once for
#: both: it talks, and what changes the project is done in a task.
KEEPS_NOTHING = (
    "你在这里的改动留不下：用 `cheese checkout` 取一份主干代码，可以读、"
    "查资料、PR 和 issue；但不推送、不交付、不摆预览。能不能跑命令和"
    "测试、临时改文件，看这台机器归不归你独占：归你独占时都能；和别的"
    "会话或机器主人共用时只能读文件，跑命令会被拒（那不是故障，重试"
    "结果一样）。要改的事用 `cheese_task(action=open)` 创建成任务。"
)

#: Where to look for what was settled elsewhere in the channel.
_SEARCH = (
    "别处定过的事不记得时，用 `cheese_chat(action=search)` 加 `channel` 搜整个频道。"
)


def _line(block) -> str:
    return f"[{speaker(block)}] {block.content}"


async def channel_context(session: "AsyncSession", room: Topic, first) -> str:
    """What a session in a channel's main line is told about where it is: the
    channel, what it may do there, and what the main line said just before
    ``first``, the oldest message it is handed. The channel's tasks are a
    section of their own (`channel_tasks`)."""
    parts = [
        f"你在频道「#{room.title}」的主线里。" + KEEPS_NOTHING + _SEARCH,
    ]
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
            f"你在频道「#{room.title}」的一条支线里。它挂在你在主线的一条消息下面，"
            f"那条消息代表例行任务「{run.get('title', '')}」的一次执行，"
            "执行完交回的结果会写进那条消息。",
            "你现在做的就是这次执行：可以保存它要的结果文件。要看代码，用 "
            "`cheese checkout` 取一份主干代码；要改代码的事用 `cheese_task(action=open)` "
            "创建成任务。"
            if routine_run
            else "这次执行已经跑过了，现在是有人在这里追问。这里的人 @ 你，你才回答。"
            + KEEPS_NOTHING,
        ]
    else:
        parts = [
            f"你在频道「#{room.title}」的一条支线里。这里的人 @ 你，你才回答。"
            + KEEPS_NOTHING
            + _SEARCH,
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
    head = "## 这个频道里还在进行的任务\n"
    if not open_tasks:
        return head + "现在没有。"
    marks = (
        "标着「从这条支线的消息建的」的任务，就是为这条支线说的事建的。"
        if root is not None
        else ""
    )
    return head + "\n".join(
        [
            "要做的事已经有任务了，就告诉人去那个任务，不再新建。" + marks,
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
