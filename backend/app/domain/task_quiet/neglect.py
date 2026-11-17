"""Tasks nobody has attended to for long enough: warn once, then close.

The clock is `room_task.presentation.neglect`: a task waiting on someone (a card
in review, a question asked) is warned after 3 days and closed after 7; any
other task is warned after 7 and closed after 14. What closes is the task, not
its PR: the branch and the PR stay, and reopening the task picks them up again.

Two sweeps, both safe to run as often as they like:

- `warn_neglected` says once, per stretch of silence, that the task will close
  and when. The delivery ledger de-duplicates it (the event's identity is the
  task plus where the silence began), so a person who speaks and goes quiet
  again is warned again, and no row is written on the task (a row would count
  as activity and the silence would never add up).
- `close_neglected` closes the ones past their time. A task whose own teammate
  has a session running is left alone: something is being done in it.

`settings.task_neglect_auto_close` is the kill switch; off, only the warning is
sent.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.sentences import notice_keys, notice_message, say
from app.domain.addressing import Event, Hand, address
from app.domain.agent.announce import announce
from app.domain.agent.teammate_runs import teammate_runs
from app.domain.block.queries import awaiting_an_answer
from app.domain.delivery.ledger import deliver
from app.domain.delivery_events import DeliveryEvent, SettleCard
from app.domain.notification.models import NotificationType
from app.domain.review.archive import close_cards_for_closed_task
from app.domain.review.ports import TaskTeammate
from app.domain.review.queries import latest_cards_by_task
from app.domain.review.task_landing import tell_origin_ended
from app.domain.room_task import heard, presentation
from app.domain.room_task.closing import StopsWork, close_task
from app.domain.room_task.lock_order import lock_task_for_change
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.ports import SweepCheckouts
from app.domain.room_task.services import TaskService
from app.domain.topic.services import TopicService

logger = logging.getLogger(__name__)

#: The warning's identity comes from task + where the silence began.
_NEGLECT = uuid.UUID("0b9e4c27-5d18-4a63-8f2e-7c1a3d6e9b54")

EVENT_TASK_NEGLECT = "task_neglect"
CLOSED_REASON_NEGLECT = "neglect"


async def _neglected(
    session: AsyncSession, now: datetime
) -> list[tuple[Task, presentation.Neglect, bool]]:
    """Open tasks on a neglect clock, each with whether its own teammate's
    session is running (then it is not neglected, only quiet)."""
    tasks = [t for t in await TaskService(session).list_open() if t.owner_handle]
    if not tasks:
        return []
    ids = [t.id for t in tasks]
    last_said = await TaskService(session).last_block_at_for_tasks(ids)
    cards = await latest_cards_by_task(session, ids)
    asked = await awaiting_an_answer(session, ids)
    runs = await teammate_runs(session, tasks)
    found = []
    for task in tasks:
        shown = presentation.task_presentation(
            presentation.facts_for_task(
                task,
                cards.get(task.id),
                awaiting_answer=task.id in asked,
                teammate_runs=runs.get(task.id),
            ),
            now=now,
        )
        clock = presentation.neglect(
            shown, last_activity=last_said.get(task.id) or task.created_at
        )
        if clock is not None:
            found.append((task, clock, task.id in runs))
    return found


async def warn_neglected(sessions, *, now: datetime | None = None) -> int:
    """Warn the owners of tasks that have gone quiet enough to be closed soon.
    Returns how many were warned this tick."""
    at = now or datetime.now(UTC)
    async with sessions() as session:
        warned = await _warn(session, at)
        await session.commit()
    return warned


async def _warn(session: AsyncSession, now: datetime) -> int:
    due = [
        (task, clock)
        for task, clock, running in await _neglected(session, now)
        if clock.dormant(now) and not clock.due(now) and not running
    ]
    if not due:
        return 0
    rooms = {
        t.id: t
        for t in await TopicService(session).get_many({t.room_id for t, _ in due})
    }
    warned = 0
    for task, clock in due:
        room = rooms.get(task.room_id)
        if room is None or str(room.status) == "archived":
            continue
        content = say(
            "taskNeglectNudge",
            title=task.title,
            days=(now - clock.since).days,
            left=max(1, (clock.close_at - now).days),
        )
        await deliver(
            session,
            DeliveryEvent(
                id=uuid.uuid5(_NEGLECT, f"{task.id}:{clock.since.isoformat()}"),
                type=NotificationType.ROOM_NOTICE,
                payload={
                    "projectId": str(task.project_id),
                    "topicId": str(room.id),
                    "topicTitle": room.title,
                    "taskId": str(task.id),
                    "content": str(content),
                    **notice_message(notice_keys(content=content)),
                    "eventType": EVENT_TASK_NEGLECT,
                    "severity": "warn",
                },
                occurred_at=now,
            ),
            address(Event(owner=task.owner_handle), Hand.participant),
        )
        warned += 1
    return warned


async def close_neglected(
    sessions,
    *,
    chat: StopsWork,
    settle_card: SettleCard | None,
    task_teammate: TaskTeammate | None,
    machines: SweepCheckouts | None = None,
    now: datetime | None = None,
) -> int:
    """Close the tasks past their neglect time. Returns how many it closed."""
    if not settings.task_neglect_auto_close:
        return 0
    at = now or datetime.now(UTC)
    async with sessions() as session:
        due = [
            (task.id, clock)
            for task, clock, running in await _neglected(session, at)
            if clock.due(at) and not running
        ]
        closed: list[Task] = []
        for task_id, clock in due:
            # Under the task lock (`lock_order`): a message being sent in the
            # task is seen below, not lost to the close.
            task = await lock_task_for_change(session, task_id)
            if task is None or task.status is not TaskStatus.open:
                continue
            days = (at - clock.since).days
            task.closed_reason = CLOSED_REASON_NEGLECT
            await heard.inform(session, task, heard.task_closed_neglected(days=days))
            await close_task(session, task, chat, machines=machines)
            await close_cards_for_closed_task(
                session, task_id=task.id, by=None, settle_card=settle_card
            )
            await announce(
                session,
                place_id=task.room_id,
                task_id=task.id,
                content=say("taskNeglectClosed", title=task.title, days=days),
                meta={
                    "platform": True,
                    "action": "task_closed_neglect",
                    "task_id": str(task.id),
                },
            )
            await tell_origin_ended(session, task, task_teammate=task_teammate)
            closed.append(task)
        await session.commit()
    if closed:
        logger.info("closed %d neglected task(s)", len(closed))
    return len(closed)
