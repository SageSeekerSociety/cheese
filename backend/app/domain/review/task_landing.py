"""What a task's delivery landing does to the task.

A task may deliver several times. The card says whether this delivery is the
last step (``AcceptCard.completes_task``). Any step but the last leaves the
task open and moves it onto a branch cut from the project's latest code, for
its AI teammate to carry on from. The last one has the AI teammate write the
task up, and the task closes when that turn ends (`room_task.closing`). Either
way the discussion the task came from hears what landed, and a merged PR is
watched on the default branch for a while (`landing_watch`).

Every path that sees a delivery merge comes through here — an accept click,
the merge queue, the poller, a PR merged by hand — so none of them can keep
closing a task the card said goes on.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_frames import show_once_committed
from app.core.sentences import say
from app.domain.agent.harness.prompt import task_next_step_prompt, task_summary_prompt
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.schemas import BlockOut
from app.domain.delivery.agent import record_task_instruction
from app.domain.delivery.ledger import DeliveryEvent
from app.domain.identity.handles import agent_instance_handle
from app.domain.notification.models import NotificationType
from app.domain.project.models import Project
from app.domain.review.landing_models import TaskLanding
from app.domain.room_task.closing import CLOSES_TASK
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.services import TaskService, said_title
from app.domain.thread.services import open_thread
from app.domain.topic.models import Topic, TopicStatus

logger = logging.getLogger(__name__)

#: How long a merge is watched on the default branch: its checks there, and a
#: deployment that includes it. A repository's CI on main and a deploy after it
#: take well under this; what has not happened by then is not reported.
WATCH_FOR = timedelta(hours=2)


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
    if pr_number is not None:
        await _watch(session, task, pr_number, now)
    goes_on = not completes and task.status == TaskStatus.open
    if goes_on:
        await TaskService(session).next_step(task)
        await tell_agent(
            session, task, task_next_step_prompt(title=task.title, task_id=task.id)
        )
    elif task.status == TaskStatus.open and task.closing_since is None:
        task.closing_since = now
        await tell_agent(
            session, task, task_summary_prompt(title=task.title), closes=True
        )
    await session.flush()
    # The room's row in the sidebar is what changed (its task count/status), so
    # name the room for the client's per-row refetch.
    show_once_committed(
        session,
        task.room_id,
        {"type": "state", "resource": "topics", "id": str(task.room_id)},
    )
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


async def _watch(
    session: AsyncSession, task: Task, pr_number: int, now: datetime
) -> None:
    """Start watching the merge of ``pr_number`` on the default branch, once."""
    if await session.scalar(
        select(TaskLanding.id).where(
            TaskLanding.task_id == task.id, TaskLanding.pr_number == pr_number
        )
    ):
        return
    session.add(
        TaskLanding(
            task_id=task.id,
            pr_number=pr_number,
            landed_at=now,
            watch_until=now + WATCH_FOR,
        )
    )


async def tell_origin(session: AsyncSession, task: Task, content: str) -> None:
    """Say ``content`` in the 支线 under the message ``task`` was made from,
    opening it if nobody has replied there yet. It is said by the task's AI
    teammate, as a reply: whoever took part in the discussion hears it, and the
    line under the message shows it. A task made on its own, or from something
    that is not a message in a live channel, has nowhere to say it.

    Best effort: what landed has landed, and a line that cannot be said must
    not take the task's own state back with it."""
    try:
        async with session.begin_nested():
            await _tell_origin(session, task, content)
    except Exception:  # noqa: BLE001 — a courtesy line never undoes a landing
        logger.warning("could not tell task %s's discussion", task.id, exc_info=True)


async def _tell_origin(session: AsyncSession, task: Task, content: str) -> None:

    if task.upgraded_from_block_id is None:
        return
    origin = await session.get(Block, task.upgraded_from_block_id)
    room = await session.get(Topic, task.room_id)
    project = await session.get(Project, task.project_id)
    if (
        origin is None
        or origin.kind != BlockKind.message
        or origin.conversation_id != task.room_id
        or room is None
        or room.status == TopicStatus.archived
        or project is None
    ):
        return
    agent = await AgentInstanceService(session).for_task(
        project, room, task.agent_handle
    )
    thread = await open_thread(
        session, origin.id, by=task.owner_handle or origin.author
    )
    said = Block(
        id=uuid.uuid4(),
        project_id=task.project_id,
        conversation_id=thread.id,
        author=agent_instance_handle(agent.instance_id),
        author_type=AuthorType.participant,
        kind=BlockKind.message,
        content=str(content),
        meta={"task_result": str(task.id)},
    )
    session.add(said)
    await session.flush()
    show_once_committed(
        session, said.conversation_id, {"type": "user_block", "block": _out(said)}
    )
    show_once_committed(session, task.room_id, {"type": "state", "resource": "threads"})


def _out(block) -> dict:

    return BlockOut.model_validate(block).model_dump(mode="json")


async def tell_agent(
    session: AsyncSession, task: Task, content: str, *, closes: bool = False
) -> None:
    """The task's AI teammate hears ``content`` on its next turn. ``closes``:
    the task closes when that turn ends."""

    payload: dict[str, object] = {
        "projectId": str(task.project_id),
        "topicId": str(task.room_id),
    }
    if closes:
        payload[CLOSES_TASK] = True
    await record_task_instruction(
        session,
        DeliveryEvent(
            id=uuid.uuid4(),
            type=NotificationType.ROOM_NOTICE,
            payload=payload,
            occurred_at=datetime.now(UTC),
        ),
        task=task,
        content=content,
    )
