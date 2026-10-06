"""What a 支线's session is told about where it is (``room/turn.py``)."""

from typing import TYPE_CHECKING

from app.domain.block.repositories import BlockRepository
from app.domain.room_task.models import TaskStatus
from app.domain.topic.models import Topic

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: How many of the main line's messages before a 支线's message its session
#: opens with: what was being talked about when it was said.
THREAD_CONTEXT_MESSAGES = 10


async def thread_context(session: "AsyncSession", room: Topic, root) -> str:
    """What a 支线's session is told about where it is: the channel, the
    message the 支线 hangs under and what the main line said just before it,
    and the channel's tasks still open — so a piece of work that already has a
    task is pointed to rather than proposed again."""
    from app.domain.room_task.services import TaskService

    def line(block) -> str:
        return f"[{block.author}] {block.content}"

    earlier = await BlockRepository(session).messages_before(
        room.id, root.created_at, limit=THREAD_CONTEXT_MESSAGES
    )
    parts = [
        f"你在频道「#{room.title}」的一条支线里。这里的人 @ 你，你才回答。"
        "你只读：可以看代码、跑只读的命令、查资料，不改项目，不交付，不摆预览；"
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
