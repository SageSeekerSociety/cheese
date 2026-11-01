"""Telling the people on a space's 题目 what happened to it.

Every notice goes through the delivery ledger (`delivery.ledger.deliver`), so
it lands in the person's 动态 and, by their preferences, in mail and push. Its
identity comes from what happened (the claim and the decision, the submission
and its review), so saying it twice is saying it once.

Who hears it: a claim made by one person tells that person; a claim made by a
team tells everyone in the team. The person who caused it is not told.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import (
    NoticeText,
    listing,
    notice_keys,
    notice_message,
    say,
)
from app.domain.addressing import Event, Hand, address
from app.domain.delivery.ledger import deliver
from app.domain.delivery_events import DeliveryEvent
from app.domain.notification.models import NotificationType
from app.domain.notification.preferences import DEFAULT_TIMEZONE, local_time
from app.domain.space.models import Space
from app.domain.task.models import Task, TaskMembership, TaskSubmission
from app.domain.task.stages import StageNews
from app.domain.team.models import TeamUserRelation
from app.domain.user.models import User

#: Event ids: one namespace per kind of notice, so the same claim decided or
#: the same submission reviewed is always the same event.
_CLAIM = uuid.UUID("2b7f6c1e-8d4a-4a53-9b1e-5d6f1c2a7e40")
_REVIEW = uuid.UUID("5c1e9a7d-3b2f-4e6a-8d0c-7a9b2e4f6c51")
_CHANGED = uuid.UUID("8e3d2c1b-6a5f-4d7e-9c8b-1a2f3e4d5c62")
_PERSONAL_DEADLINE = uuid.UUID("a4c2e6b8-1d3f-4a5c-8e7b-9d1f2a3c4e73")


async def claimers(
    session: AsyncSession, memberships: Iterable[TaskMembership]
) -> dict[int, set[int]]:
    """Membership id -> the user ids it stands for: the person, or the team's
    members."""
    rows = list(memberships)
    teams = {m.member_id for m in rows if m.is_team}
    members: dict[int, set[int]] = {}
    if teams:
        found = await session.execute(
            select(TeamUserRelation.team_id, TeamUserRelation.user_id).where(
                TeamUserRelation.team_id.in_(teams),
                TeamUserRelation.deleted_at.is_(None),
            )
        )
        for team_id, user_id in found:
            members.setdefault(int(team_id), set()).add(int(user_id))
    return {
        m.id: (members.get(m.member_id, set()) if m.is_team else {int(m.member_id)})
        for m in rows
    }


async def _handles(session: AsyncSession, user_ids: Iterable[int]) -> tuple[str, ...]:
    ids = sorted(set(user_ids))
    if not ids:
        return ()
    found = await session.scalars(
        select(User.username).where(User.id.in_(ids), User.deleted_at.is_(None))
    )
    return tuple(sorted(found))


def payload(task: Task, space: Space | None, content: NoticeText, **more) -> dict:
    """What a notice about a 题目 carries: where it is, the sentence (with its
    key, so each reader's screen says it in their language) and what kind of
    thing happened (``event``)."""
    return {
        "spaceId": task.space_id,
        "spaceName": space.name if space else "",
        "spaceTaskId": task.id,
        "taskName": task.name,
        "content": str(content),
        **notice_message(notice_keys(content=content)),
        **more,
    }


async def tell(
    session: AsyncSession,
    *,
    event_id: uuid.UUID,
    type_: NotificationType,
    body: dict,
    user_ids: Iterable[int],
    at: datetime,
    leave_out: int | None = None,
) -> int:
    """Deliver one notice to these people (but ``leave_out``, who caused it).
    Returns how many were told."""
    handles = await _handles(session, (u for u in user_ids if u != leave_out))
    if not handles:
        return 0
    await deliver(
        session,
        DeliveryEvent(id=event_id, type=type_, payload=body, occurred_at=at),
        address(Event(audience=handles), Hand.participant),
    )
    return len(handles)


async def claim_decided(
    session: AsyncSession,
    *,
    task: Task,
    membership: TaskMembership,
    approved: bool,
    by: int,
    at: datetime,
) -> None:
    """The publisher approved or turned down a claim."""
    space = await session.get(Space, task.space_id)
    content = say(
        "spaceTaskClaimApproved" if approved else "spaceTaskClaimRejected",
        task=task.name,
    )
    people = (await claimers(session, [membership]))[membership.id]
    await tell(
        session,
        event_id=uuid.uuid5(_CLAIM, f"{membership.id}:{'yes' if approved else 'no'}"),
        type_=NotificationType.TASK_NOTICE,
        body=payload(
            task, space, content, event="claimApproved" if approved else "claimRejected"
        ),
        user_ids=people,
        at=at,
        leave_out=by,
    )


async def reviewed(
    session: AsyncSession,
    *,
    task: Task,
    membership: TaskMembership,
    submission_id: int,
    accepted: bool,
    score: int,
    by: int,
    at: datetime,
) -> None:
    """A submission was reviewed (or reviewed again: the notice says the latest
    verdict, once per verdict)."""
    space = await session.get(Space, task.space_id)
    submission = await session.get(TaskSubmission, submission_id)
    version = submission.version if submission else 0
    content = say(
        "spaceTaskReviewPassed" if accepted else "spaceTaskReviewFailed",
        task=task.name,
        score=score,
    )
    people = (await claimers(session, [membership]))[membership.id]
    await tell(
        session,
        event_id=uuid.uuid5(
            _REVIEW, f"{submission_id}:{'yes' if accepted else 'no'}:{score}"
        ),
        type_=NotificationType.TASK_NOTICE,
        body=payload(
            task,
            space,
            content,
            event="reviewed",
            accepted=accepted,
            score=score,
            version=version,
            participantId=membership.id,
        ),
        user_ids=people,
        at=at,
        leave_out=by,
    )


def _change_clauses(
    renamed_to: str | None, requirements: bool, stages: StageNews, zone: str | None
) -> list[NoticeText]:
    """What changed, one clause each, due times on the reader's clock."""
    clauses = []
    if renamed_to is not None:
        clauses.append(say("spaceTaskChangedName", name=renamed_to))
    if requirements:
        clauses.append(say("spaceTaskChangedRequirements"))
    for before, after in stages.renamed:
        clauses.append(say("spaceTaskChangedStageName", stage=before, name=after))
    if stages.added:
        clauses.append(
            say(
                "spaceTaskChangedStagesAdded", stages=listing(stages.added, quoted=True)
            )
        )
    if stages.removed:
        clauses.append(
            say(
                "spaceTaskChangedStagesRemoved",
                stages=listing(stages.removed, quoted=True),
            )
        )
    for name, due_ms in stages.due_moved:
        if due_ms is None:
            clauses.append(say("spaceTaskChangedStageDueCleared", stage=name))
            continue
        due = local_time(datetime.fromtimestamp(due_ms / 1000, tz=UTC), zone)
        clauses.append(
            say(
                "spaceTaskChangedStageDue",
                stage=name,
                due=f"{due:%Y-%m-%d %H:%M}",
                zone=_zone_name(due.tzinfo),
            )
        )
    return clauses


def _zone_name(zone: object) -> NoticeText | str:
    """Beijing time by its name in the reader's language; any other zone by its
    IANA name, which reads the same in every language."""
    name = str(zone)
    return say("timeZoneBeijing") if name == DEFAULT_TIMEZONE else name


async def task_changed(
    session: AsyncSession,
    *,
    task: Task,
    old_name: str,
    requirements: bool,
    stages: StageNews,
    by: int,
    at: datetime,
) -> None:
    """The 题目's name, what to hand in, how it is scored, or which stages there
    are, what they are called and when they are due changed: everyone who
    claimed the 题目 and was not turned down hears it, in one notice that says
    what changed. The notice names the 题目 as they knew it (``old_name``), so a
    renamed one reads 「旧名」改名为「新名」. A stage's new due time is written on
    each reader's own clock."""
    renamed_to = task.name if task.name != old_name else None
    if not (renamed_to or requirements or stages):
        return
    memberships = list(
        await session.scalars(
            select(TaskMembership).where(
                TaskMembership.task_id == task.id,
                TaskMembership.deleted_at.is_(None),
                TaskMembership.approved != 1,
            )
        )
    )
    if not memberships:
        return
    space = await session.get(Space, task.space_id)
    people = {u for ids in (await claimers(session, memberships)).values() for u in ids}
    # A due time is said on the reader's clock, so readers on different clocks
    # get the sentence each can read; the rest of it is the same for all.
    by_zone: dict[str | None, set[int]] = {}
    if stages.due_moved and people:
        rows = await session.execute(
            select(User.id, User.timezone).where(User.id.in_(people))
        )
        for user_id, zone in rows:
            by_zone.setdefault(zone, set()).add(int(user_id))
    else:
        by_zone[None] = people
    for zone, user_ids in by_zone.items():
        content = say(
            "spaceTaskChanged",
            task=old_name,
            changes=listing(_change_clauses(renamed_to, requirements, stages, zone)),
        )
        await tell(
            session,
            event_id=uuid.uuid5(_CHANGED, f"{task.id}:{at.isoformat()}:{zone or ''}"),
            type_=NotificationType.TASK_NOTICE,
            body=payload(task, space, content, event="requirementsChanged"),
            user_ids=user_ids,
            at=at,
            leave_out=by,
        )


async def personal_deadline_changed(
    session: AsyncSession,
    *,
    task: Task,
    membership: TaskMembership,
    by: int,
    at: datetime,
) -> None:
    """The publisher moved one claim's submission deadline."""
    if membership.deadline is None:
        return
    space = await session.get(Space, task.space_id)
    people = (await claimers(session, [membership]))[membership.id]
    deadline_ms = int(membership.deadline.timestamp() * 1000)
    await tell(
        session,
        event_id=uuid.uuid5(_PERSONAL_DEADLINE, f"{membership.id}:{deadline_ms}"),
        type_=NotificationType.TASK_NOTICE,
        body=payload(
            task,
            space,
            say("spaceTaskDeadlineMoved", task=task.name),
            event="deadlineMoved",
            deadline=deadline_ms,
        ),
        user_ids=people,
        at=at,
        leave_out=by,
    )
