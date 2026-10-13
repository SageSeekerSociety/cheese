"""一个人一道题只领一次。

A claim is live unless it was rejected (``approved == 1``) or withdrawn
(``deleted_at``), so a pending application counts: its workspace was opened
the moment it was made.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domain.task.models import Task, TaskMembership
from app.domain.task.repositories import TaskMembershipRepository
from app.domain.team.models import Team, TeamUserRelation


async def members_claiming_through_another_team(
    session: AsyncSession, *, task_id: int, team_id: int
) -> list[int]:
    """Members of ``team_id`` who already hold a live claim on this task
    through a different team."""
    other_member = aliased(TeamUserRelation)
    stmt = select(TeamUserRelation.user_id).where(
        TeamUserRelation.team_id == team_id,
        TeamUserRelation.deleted_at.is_(None),
        exists().where(
            TaskMembership.task_id == task_id,
            TaskMembership.is_team.is_(True),
            TaskMembership.member_id != team_id,
            TaskMembership.approved != 1,
            TaskMembership.deleted_at.is_(None),
            other_member.team_id == TaskMembership.member_id,
            other_member.user_id == TeamUserRelation.user_id,
            other_member.deleted_at.is_(None),
        ),
    )
    return sorted(set((await session.execute(stmt)).scalars().all()))


def own_claim(
    task: Task,
    user_membership: TaskMembership | None,
    team_memberships: Sequence[TaskMembership],
) -> TaskMembership | None:
    """The claim a person stands on for this task: their own for an individual
    task, one of their teams' for a team task. An approved claim wins over one
    still waiting; a rejected one is no claim at all.

    Its ``deadline`` is the person's deadline — set when the claim is approved
    (approval time plus the task's 提交期限) and movable by the publisher
    afterwards. Every place that says "your deadline" reads it from here.
    """
    candidates = (
        [user_membership]
        if task.submitter_type == 0 and user_membership is not None
        else list(team_memberships)
        if task.submitter_type == 1
        else []
    )
    live = [m for m in candidates if m.approved != 1]
    approved = [m for m in live if m.approved == 0]
    return (approved or live or [None])[0]


async def claim_of(
    session: AsyncSession, *, task: Task, user_id: int
) -> TaskMembership | None:
    """``own_claim`` for one person, read from the database."""
    repo = TaskMembershipRepository(session=session)
    return own_claim(
        task,
        await repo.get_user_membership(task_id=task.id, user_id=user_id),
        await repo.list_team_memberships_for_user(task_id=task.id, user_id=user_id),
    )


@dataclass(frozen=True)
class ProjectDeadline:
    """When the project a claim opened has to hand in by."""

    at: datetime
    #: The claim's own deadline (set at approval); False when the claim has
    #: none yet and this is the challenge's closing time instead.
    mine: bool


async def deadline_for_project(
    session: AsyncSession, *, task_id: int, team_id: int | None
) -> ProjectDeadline | None:
    """The deadline of the claim a project was opened for: a team claim is the
    project's team's, an individual one is the claim of the person whose
    personal team holds the project (``ProjectService.for_participation``).
    Approved before pending, never rejected — the same order as ``own_claim``.
    """
    task = await session.get(Task, task_id)
    if task is None:
        return None
    owner = (await session.get(Team, team_id)) if team_id is not None else None
    person = owner.personal_owner_user_id if owner is not None else None
    whose = [
        and_(TaskMembership.is_team.is_(True), TaskMembership.member_id == team_id)
    ]
    if person is not None:
        whose.append(
            and_(TaskMembership.is_team.is_(False), TaskMembership.member_id == person)
        )
    claims = (
        await session.execute(
            select(TaskMembership).where(
                TaskMembership.task_id == task_id,
                TaskMembership.deleted_at.is_(None),
                TaskMembership.approved != 1,
                or_(*whose),
            )
        )
    ).scalars()
    claim = min(claims, key=lambda m: m.approved != 0, default=None)
    if claim is not None and claim.approved == 0 and claim.deadline is not None:
        return ProjectDeadline(at=claim.deadline, mine=True)
    if task.deadline is not None:
        return ProjectDeadline(at=task.deadline, mine=False)
    return None
