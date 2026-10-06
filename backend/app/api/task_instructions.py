"""Telling a task's own session something: what a new task comes from, that
its owner started it. Kept in the delivery ledger until it is delivered, so an
instruction outlives a backend that is replaced before it is sent."""

import uuid
from datetime import UTC, datetime

from app.api.deps import get_work_runner
from app.api.routes.topics import BlockRepository
from app.domain.agent.chat import ChatService

#: How many messages before the one a task comes from its agent is shown: what
#: was being talked about when it was said.
SOURCE_CONTEXT_MESSAGES = 8
#: How many room messages before a proposal its task's agent is shown. A
#: proposal closes a discussion rather than quoting one line of it, and what
#: was decided along the way is the document's to keep.
PROPOSAL_CONTEXT_MESSAGES = 20
#: How many of a 支线's replies a task made from it is handed. A 支线 is the
#: whole discussion, so all of it, up to where a prompt would be mostly history.
THREAD_SOURCE_MESSAGES = 60


def _lines(blocks) -> str:
    return "\n".join(f"[{b.author}] {b.content}" for b in blocks)


async def source_text(db, block) -> str:
    """The message a task comes from, with what was said just before it, and
    its 支线 when it has one."""
    from app.domain.thread.models import Thread

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
            f"（前面还有 {left} 条回复没列出，用 cheese_chat_list 读支线 {thread.id}）"
        )
    lines.append(_lines(replies))
    return "\n".join(line for line in lines if line)


async def proposal_source_text(db, proposal) -> str:
    """What a proposed task comes from: the proposal, and the discussion that
    led to it — the whole 支线 it was proposed in, or the main line's last
    messages."""
    from app.domain.thread.models import Thread

    thread = await db.get(Thread, proposal.conversation_id)
    said = f"[{proposal.proposed_by}] 提议：{proposal.summary}"
    if thread is not None:
        return f"{said}\n\n提议之前的讨论：\n{await thread_text(db, thread)}"
    earlier = await BlockRepository(db).messages_before(
        proposal.conversation_id, proposal.created_at, limit=PROPOSAL_CONTEXT_MESSAGES
    )
    if not earlier:
        return said
    return f"{said}\n\n提议之前的讨论：\n{_lines(earlier)}"


async def tell_task(db, task, content: str, *, opening: bool = False) -> None:
    """Keep an instruction for the task's own session; dispatched after commit
    (`dispatch`). ``opening`` marks the first one, which drafts the task's
    document from where it came from (`agent/opening.py`)."""
    from app.domain.agent.opening import OPENING, PURPOSE
    from app.domain.delivery.agent import record_task_instruction
    from app.domain.delivery.ledger import DeliveryEvent
    from app.domain.notification.models import NotificationType

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
        task=task,
        content=content,
    )


async def dispatch(chat: ChatService) -> None:
    from app.domain.delivery.agent import dispatch_pending

    await dispatch_pending(chat.session_factory, chat=chat, runner=get_work_runner())
