"""Telling a task's own session something: what a new task comes from. Kept in
the delivery ledger until it is delivered, so an instruction outlives a backend
that is replaced before it is sent. The line a 支线 that became a task shows is
said from here too, so the two ways it becomes one — a person turning it, or its
AI teammate doing so on someone's word — say the same sentence."""

import uuid
from datetime import UTC, datetime

from app.api.deps import get_work_runner
from app.api.topics_deps import BlockRepository
from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.chat import ChatService
from app.domain.agent.opening import OPENING, PURPOSE
from app.domain.delivery.agent import dispatch_pending, record_task_instruction
from app.domain.delivery_events import DeliveryEvent
from app.domain.notification.models import NotificationType
from app.domain.thread.models import Thread

#: How many messages before the one a task comes from its agent is shown: what
#: was being talked about when it was said.
SOURCE_CONTEXT_MESSAGES = 8
#: How many of a 支线's replies a task made from it is handed. A 支线 is the
#: whole discussion, so all of it, up to where a prompt would be mostly history.
THREAD_SOURCE_MESSAGES = 60


def _lines(blocks) -> str:
    return "\n".join(f"[{b.author}] {b.content}" for b in blocks)


async def source_text(db, block) -> str:
    """The message a task comes from, with what was said just before it, and
    its 支线 when it has one."""
    earlier = await BlockRepository(db).messages_before(
        block.conversation_id, block.created_at, limit=SOURCE_CONTEXT_MESSAGES
    )
    said = _lines([*earlier, block])
    thread = await Thread.of_root(db, block.id)
    if thread is None or not thread.reply_count:
        return said
    return f"{said}\n\n这条消息下的支线：\n{await thread_text(db, thread, root=False)}"


async def thread_text(db, thread, *, root: bool = True) -> str:
    """A 支线 as a task's agent reads it: the message it hangs under and every
    reply, or the latest ones and where to read the rest."""
    repo = BlockRepository(db)
    replies = await repo.messages_before(
        thread.id, datetime.now(UTC), limit=THREAD_SOURCE_MESSAGES
    )
    lines = []
    if root and (first := await repo.get(thread.root_block_id)) is not None:
        lines.append(_lines([first]))
    left = thread.reply_count - len(replies)
    if left > 0:
        lines.append(
            f"（前面还有 {left} 条回复没列出，用 cheese_chat(action=list) 读支线 {thread.id}）"
        )
    lines.append(_lines(replies))
    return "\n".join(line for line in lines if line)


def teammate_source_text(teammate: str, summary: str, discussion) -> str:
    """What a task an AI teammate created comes from: what it wrote the task
    is for, and the discussion it was in."""
    said = f"[{teammate}] 创建任务时写的：{summary}"
    if not discussion:
        return said
    return f"{said}\n\n创建之前的讨论：\n{_lines(discussion)}"


async def tell_task(db, task, content: str, *, opening: bool = False) -> None:
    """Keep an instruction for the task's own session; dispatched after commit
    (`dispatch`). ``opening`` marks the first one, which drafts the task's
    document from where it came from (`agent/opening.py`)."""
    payload = {"projectId": str(task.project_id), "topicId": str(task.room_id)}
    if opening:
        payload[PURPOSE] = OPENING
    await record_task_instruction(
        db,
        DeliveryEvent(
            id=uuid.uuid4(),
            type=NotificationType.ROOM_NOTICE,
            payload=payload,
            occurred_at=datetime.now(UTC),
        ),
        task_id=task.id,
        content=content,
    )


async def dispatch(chat: ChatService) -> None:
    await dispatch_pending(chat.session_factory, chat=chat, runner=get_work_runner())


async def say_became_task(db, task) -> None:
    """「已转为任务，某某 负责」，说在这段对话里：从这一行起它是一个任务，谁负责。
    AI 队友怎么知道：人转的，转的那一刻交给它一条指令（`tell_task`）；它自己转的，
    读工具的回复。两样说的都是 `became_task_prompt`。"""
    await announce(
        db,
        place_id=task.room_id,
        task_id=task.id,
        content=say("threadBecameTask", owner=f"<@{task.owner_handle}>"),
        meta={
            "platform": True,
            "action": "thread_became_task",
            "task_id": str(task.id),
        },
    )
