"""Telling a task's own session something: what a new task comes from, that
its owner started it. Kept in the delivery ledger until it is delivered, so an
instruction outlives a backend that is replaced before it is sent. What the
room is told when a task starts is said from here too, so the two ways a task
starts — its owner saying so, or being made already started — say the same
sentence."""

import uuid
from datetime import UTC, datetime

from app.api.deps import get_work_runner
from app.api.topics_deps import BlockRepository
from app.core.errors import UnprocessableEntityError
from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.chat import ChatService
from app.domain.room_task.services import TaskService, said_title

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
    from app.domain.agent.opening import OPENING, PURPOSE
    from app.domain.delivery.agent import record_task_instruction
    from app.domain.delivery_events import DeliveryEvent
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
        task_id=task.id,
        content=content,
    )


async def dispatch(chat: ChatService) -> None:
    from app.domain.delivery.agent import dispatch_pending

    await dispatch_pending(chat.session_factory, chat=chat, runner=get_work_runner())


async def say_task_started(db, task, *, actor: str) -> None:
    """「任务已开始」说在任务自己的对话里：谁让它开始的，它当时叫什么名字。

    人在任务页点「开始」和平台建出来就顺手开始，说的是同一句 —— 后者只是把那一下
    提前到建它的时候。名字取的是此刻的：还没命名的任务在开始那一刻名的是占位符，
    不是后来那个名字（``said_title``）。
    """
    await announce(
        db,
        place_id=task.room_id,
        task_id=task.id,
        content=say("taskStarted", actor=f"<@{actor}>", title=said_title(task)),
        meta={"platform": True, "action": "task_started", "task_id": str(task.id)},
    )


async def start_task_at_once(db, task, *, by: str) -> str | None:
    """分支任务建出来即已开始：审阅人由 ``TaskService.start`` 按档定（负责人那一
    档不是人时落项目默认），开始时刻和当时那版文档照记 —— 改动的对照面还是开始
    那一刻。那句话照说，说进任务自己的对话，和人点「开始」时一模一样。

    人手动新建的空任务与芝士受人所托建的任务都走这里，两者只差负责人是谁。芝士
    自想的那种（``start=false``）不走：它停在讨论中，等负责人说要做。

    不成立时返回没开始的理由（沿用 ``start`` 的话），任务留在讨论中等负责人；这
    与 `POST /start` 报同一句话，所以人自己点「开始」时看到的是同一件事。没开始
    就没那句话 —— 这一刻没发生的事不预先说。
    """
    try:
        await TaskService(db).start(task, by=by)
    except UnprocessableEntityError as exc:
        return str(exc)
    await say_task_started(db, task, actor=by)
    return None
