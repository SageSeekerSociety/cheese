"""Submission deadlines: reminding who has not handed in, telling the publisher
who missed one.

A claim's own deadline (``TaskMembership.deadline``, set when it is approved)
is what counts, not the 题目's registration deadline. Two reminders go to a
claim with nothing handed in: three days before, and a day before (that one
goes to phones and desktops too). A claim approved with less than a day left
gets only the second.

Once a deadline passes with nothing handed in, the publisher hears how many
claims are past their deadline without a submission; another one passing says
it again with the new count. Only deadlines passed in the last week count, so a
题目 from last term stays quiet.

"Once" is the delivery ledger's: each reminder's identity is the claim, the
deadline and which of the two it is, so the hourly sweep says each once, and a
moved deadline is reminded of again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.domain.notification.models import NotificationType
from app.domain.space.models import Space
from app.domain.task.models import Task, TaskMembership, TaskSubmission
from app.domain.task_notices.notices import claimers, payload, tell

#: The two reminders before a deadline.
EARLY = timedelta(days=3)
LATE = timedelta(days=1)
#: How far back a missed deadline is still told to the publisher.
MISSED_WINDOW = timedelta(days=7)

_DUE = uuid.UUID("c7e1a3d5-2f4b-4c6e-8a9d-3b5f7e1c2a84")
_MISSED = uuid.UUID("e9b3d5f7-4a6c-4e8f-9b1d-5c7e9a3b4d95")


async def remind_deadlines(sessions, *, now: datetime | None = None) -> int:
    """Remind the claims coming due and tell publishers of missed ones.
    Returns how many notices went out this tick."""
    at = now or datetime.now(UTC)
    async with sessions() as session:
        told = await _due(session, at) + await _missed(session, at)
        await session.commit()
    return told


def _nothing_handed_in():
    return ~exists().where(
        TaskSubmission.membership_id == TaskMembership.id,
        TaskSubmission.deleted_at.is_(None),
    )


async def _due(session: AsyncSession, now: datetime) -> int:
    rows = (
        await session.execute(
            select(TaskMembership, Task)
            .join(Task, Task.id == TaskMembership.task_id)
            .where(
                TaskMembership.deleted_at.is_(None),
                TaskMembership.approved == 0,
                TaskMembership.deadline.is_not(None),
                TaskMembership.deadline > now,
                TaskMembership.deadline <= now + EARLY,
                Task.deleted_at.is_(None),
                Task.ended_at.is_(None),
                _nothing_handed_in(),
            )
        )
    ).all()
    if not rows:
        return 0
    people = await claimers(session, [m for m, _ in rows])
    spaces = {
        s.id: s
        for s in await session.scalars(
            select(Space).where(Space.id.in_({t.space_id for _, t in rows}))
        )
    }
    told = 0
    for membership, task in rows:
        assert membership.deadline is not None
        late = membership.deadline - now <= LATE
        which = "late" if late else "early"
        told += await tell(
            session,
            event_id=uuid.uuid5(
                _DUE, f"{membership.id}:{membership.deadline.isoformat()}:{which}"
            ),
            type_=NotificationType.TASK_DUE_SOON
            if late
            else NotificationType.TASK_NOTICE,
            body=payload(
                task,
                spaces.get(task.space_id),
                say(
                    "spaceTaskDueTomorrow" if late else "spaceTaskDueInDays",
                    task=task.name,
                ),
                event="dueSoon",
                deadline=int(membership.deadline.timestamp() * 1000),
            ),
            user_ids=people.get(membership.id, set()),
            at=now,
        )
    return told


async def _missed(session: AsyncSession, now: datetime) -> int:
    rows = (
        await session.execute(
            select(Task, TaskMembership.deadline)
            .join(TaskMembership, TaskMembership.task_id == Task.id)
            .where(
                TaskMembership.deleted_at.is_(None),
                TaskMembership.approved == 0,
                TaskMembership.deadline <= now,
                TaskMembership.deadline > now - MISSED_WINDOW,
                Task.deleted_at.is_(None),
                Task.ended_at.is_(None),
                _nothing_handed_in(),
            )
        )
    ).all()
    if not rows:
        return 0
    missed: dict[int, tuple[Task, list[datetime]]] = {}
    for task, deadline in rows:
        missed.setdefault(task.id, (task, []))[1].append(deadline)
    spaces = {
        s.id: s
        for s in await session.scalars(
            select(Space).where(Space.id.in_({t.space_id for t, _ in rows}))
        )
    }
    told = 0
    for task, deadlines in missed.values():
        if task.creator_id is None:
            continue
        latest = max(deadlines)
        told += await tell(
            session,
            event_id=uuid.uuid5(_MISSED, f"{task.id}:{latest.isoformat()}"),
            type_=NotificationType.TASK_NOTICE,
            body=payload(
                task,
                spaces.get(task.space_id),
                say("spaceTaskMissed", task=task.name, n=len(deadlines)),
                event="missed",
                count=len(deadlines),
            ),
            user_ids={int(task.creator_id)},
            at=now,
        )
    return told
