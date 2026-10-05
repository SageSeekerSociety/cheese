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


def _lines(blocks) -> str:
    return "\n".join(f"[{b.author}] {b.content}" for b in blocks)


async def source_text(db, block) -> str:
    """The message a task comes from, with what was said just before it."""
    earlier = await BlockRepository(db).messages_before(
        block.conversation_id, block.created_at, limit=SOURCE_CONTEXT_MESSAGES
    )
    return _lines([*earlier, block])


async def proposal_source_text(db, proposal) -> str:
    """What a proposed task comes from: the proposal, and the room's
    discussion that led to it."""
    earlier = await BlockRepository(db).messages_before(
        proposal.room_id, proposal.created_at, limit=PROPOSAL_CONTEXT_MESSAGES
    )
    said = f"[{proposal.proposed_by}] 提议：{proposal.summary}"
    if not earlier:
        return said
    return f"{said}\n\n提议之前房间里的讨论：\n{_lines(earlier)}"


async def tell_task(db, task, content: str) -> None:
    """Keep an instruction for the task's own session; dispatched after commit
    (`dispatch`)."""
    from app.domain.delivery.agent import record_task_instruction
    from app.domain.delivery.ledger import DeliveryEvent
    from app.domain.notification.models import NotificationType

    await record_task_instruction(
        db,
        DeliveryEvent(
            id=uuid.uuid4(),
            type=NotificationType.ROOM_NOTICE,
            payload={"projectId": str(task.project_id), "topicId": str(task.room_id)},
            occurred_at=datetime.now(UTC),
        ),
        task=task,
        content=content,
    )


async def dispatch(chat: ChatService) -> None:
    from app.domain.delivery.agent import dispatch_pending

    await dispatch_pending(chat.session_factory, chat=chat, runner=get_work_runner())
