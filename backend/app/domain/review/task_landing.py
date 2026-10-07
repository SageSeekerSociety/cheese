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


async def tell_origin(session: AsyncSession, task: Task, content) -> None:
    """Say ``content`` in the 支线 under the message ``task`` was made from,
    opening it if nobody has replied there yet. A task made on its own has
    nowhere to say it."""
    from app.domain.agent.announce import announce
    from app.domain.block.models import Block
    from app.domain.thread.services import open_thread

    if task.upgraded_from_block_id is None:
        return
    origin = await session.get(Block, task.upgraded_from_block_id)
    if origin is None or origin.conversation_id != task.room_id:
        return
    thread = await open_thread(
        session, origin.id, by=task.owner_handle or origin.author
    )
    await announce(
        session,
        place_id=task.room_id,
        task_id=thread.id,
        content=content,
        meta={"platform": True, "action": "task_result", "task_id": str(task.id)},
    )


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
