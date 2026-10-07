"""What a task's delivery landing does to the task.

A task may deliver several times. The card says whether this delivery is the
last step (``AcceptCard.completes_task``): the last one closes the task, any
other leaves it open and moves it onto a branch cut from the project's latest
code, for its AI teammate to carry on from. Either way the discussion the task
came from hears what landed.

Every path that sees a delivery merge comes through here — an accept click,
the merge queue, the poller, a PR merged by hand — so none of them can keep
closing a task the card said goes on.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.domain.room_task.checkouts import after_close
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.services import TaskService, said_title


async def delivery_landed(
    session: AsyncSession,
    task: Task,
    *,
    by: str | None,
    head: str | None,
    pr_number: int | None,
    completes: bool,
) -> None:
    """``task``'s delivery merged. ``completes``: it was the task's last step."""
    now = datetime.now(UTC)
    task.accepted_at = now
    task.accepted_by = by or task.accepted_by
    if head:
        task.delivered_head = head[:64]
    goes_on = not completes and task.status == TaskStatus.open
    if goes_on:
        await TaskService(session).next_step(task)
        await _tell_next_step(session, task)
    elif task.status != TaskStatus.closed:
        task.status = TaskStatus.closed
        task.closed_at = now
        after_close(session, task.room_id)
    await session.flush()
    _after_commit(session, task.room_id, {"type": "state", "resource": "topics"})
    await tell_origin(
        session,
        task,
        say("taskStepLanded", title=said_title(task), pr=pr_number)
        if goes_on and pr_number is not None
        else say("taskStepLandedNoPr", title=said_title(task))
        if goes_on
        else say("taskLanded", title=said_title(task), pr=pr_number)
        if pr_number is not None
        else say("taskLandedNoPr", title=said_title(task)),
    )


async def tell_origin(session: AsyncSession, task: Task, content: str) -> None:
    """Say ``content`` in the 支线 under the message ``task`` was made from,
    opening it if nobody has replied there yet. It is said by the task's AI
    teammate, as a reply: whoever took part in the discussion hears it, and the
    line under the message shows it. A task made on its own has nowhere to say
    it."""
    from app.domain.agent_instance.services import AgentInstanceService
    from app.domain.block.models import AuthorType, Block, BlockKind
    from app.domain.block.repositories import BlockRepository
    from app.domain.identity.handles import agent_instance_handle
    from app.domain.project.models import Project
    from app.domain.thread.services import open_thread
    from app.domain.topic.models import Topic

    if task.upgraded_from_block_id is None:
        return
    origin = await session.get(Block, task.upgraded_from_block_id)
    room = await session.get(Topic, task.room_id)
    project = await session.get(Project, task.project_id)
    if origin is None or origin.conversation_id != task.room_id or room is None:
        return
    if project is None:
        return
    agent = await AgentInstanceService(session).for_task(
        project, room, task.agent_handle
    )
    thread = await open_thread(
        session, origin.id, by=task.owner_handle or origin.author
    )
    said = await BlockRepository(session).add(
        project_id=task.project_id,
        conversation_id=thread.id,
        author=agent_instance_handle(agent.instance_id),
        author_type=AuthorType.participant,
        kind=BlockKind.message,
        content=str(content),
        meta={"task_result": str(task.id)},
    )
    _after_commit(
        session, said.conversation_id, {"type": "user_block", "block": _out(said)}
    )
    _after_commit(session, task.room_id, {"type": "state", "resource": "threads"})


def _out(block) -> dict:
    from app.domain.block.schemas import BlockOut

    return BlockOut.model_validate(block).model_dump(mode="json")


def _after_commit(session: AsyncSession, channel, frame: dict) -> None:
    """Send ``frame`` to the pages open on ``channel`` once this commits."""
    from app.domain.agent.announce import SHOW_ONCE_COMMITTED

    session.info.setdefault(SHOW_ONCE_COMMITTED, []).append((str(channel), frame))


async def _tell_next_step(session: AsyncSession, task: Task) -> None:
    """The task's AI teammate hears that a step landed and it goes on."""
    import uuid

    from app.domain.agent.harness.prompt import task_next_step_prompt
    from app.domain.delivery.agent import record_task_instruction
    from app.domain.delivery.ledger import DeliveryEvent
    from app.domain.notification.models import NotificationType

    await record_task_instruction(
        session,
        DeliveryEvent(
            id=uuid.uuid4(),
            type=NotificationType.ROOM_NOTICE,
            payload={"projectId": str(task.project_id), "topicId": str(task.room_id)},
            occurred_at=datetime.now(UTC),
        ),
        task=task,
        content=task_next_step_prompt(title=task.title, task_id=task.id),
    )
