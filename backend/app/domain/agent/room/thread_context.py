"""What a 支线's session is told about where it is (``room/turn.py``)."""

from typing import TYPE_CHECKING

from app.domain.room_task.models import TaskStatus
from app.domain.thread.reads import routine_runs_of, said_before
from app.domain.topic.models import Topic

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: How many of the main line's messages before a 支线's message its session
#: opens with: what was being talked about when it was said.
THREAD_CONTEXT_MESSAGES = 10


async def thread_context(
    session: "AsyncSession", room: Topic, root, *, routine_run: bool = False
) -> str:
    """What a 支线's session is told about where it is: the channel, the
    message the 支线 hangs under and what the main line said just before it,
    and the channel's tasks still open — so a piece of work that already has a
    task is pointed to rather than proposed again. A routine's run is told
    that this turn may keep what it produces, and that the 支线 keeps nothing
    afterwards."""
    from app.domain.room_task.services import TaskService

    run = (await routine_runs_of(session, [root.id])).get(root.id)

    def line(block) -> str:
        return f"[{block.author}] {block.content}"

    earlier = await said_before(session, root, limit=THREAD_CONTEXT_MESSAGES)
    if run:
        opening = [
            f"你在频道「#{room.title}」的一条支线里。它挂在你在主线的一条消息下面，"
            f"那条消息代表例行任务「{run.get('title', '')}」的一次执行，"
            "执行完交回的结果会写进那条消息。",
            "这一轮就是这次执行：可以保存它要的结果文件。"
            if routine_run
            else "这次执行已经跑过了，现在是有人在这里追问。这里的人 @ 你，你才回答。"
            "你在这里的改动留不下：可以读代码、跑命令和测试、临时改文件、查资料，"
            "但不推送、不交付、不摆预览；"
            "要改的事用 `cheese_task` 提议成任务。",
        ]
        parts = [*opening]
    else:
        parts = [
            f"你在频道「#{room.title}」的一条支线里。这里的人 @ 你，你才回答。"
            "你在这里的改动留不下：可以读代码、跑命令和测试、临时改文件、查资料，"
            "但不推送、不交付、不摆预览；"
            "要改的事用 `cheese_task` 提议成任务。别处定过的事不记得时，用 "
            "`cheese_chat_search` 加 `channel` 搜整个频道。",
            "",
            "支线挂在主线的这条消息下面：",
            line(root),
        ]
    if earlier:
        parts += ["", "这条消息之前，主线上说的是：", *map(line, earlier)]
    open_tasks = [
        task
        for task in await TaskService(session).list_in_room(room.id)
        if task.status == TaskStatus.open
    ]
    if open_tasks:
        parts += [
            "",
            "这个频道里还在进行的任务（要做的事已经有任务了，就告诉人去那个任务，"
            "不再提议）：",
            *(
                f"- {task.title}"
                + (f"（负责人 @{task.owner_handle}）" if task.owner_handle else "")
                for task in open_tasks
            ),
        ]
    return "\n".join(parts)
