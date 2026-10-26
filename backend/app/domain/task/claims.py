"""一个人一道题只领一次。

A claim is live unless it was rejected (``approved == 1``) or withdrawn
(``deleted_at``), so a pending application counts: nobody holds a second one
while it waits.

How a claim starts is the task's ``start_mode``: a task started directly takes
a claim already approved, a task applied for takes it pending (``starts_now``,
``deadline_from``). Either way the claim's project is made when its claimant
starts it, once it is approved (``app.api.task_claims``).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import BadRequestError
from app.core.sentences import say
from app.domain.project.models import Project
from app.domain.task.inputs import (
    APPROVE_APPROVED,
    APPROVE_DISAPPROVED,
    START_DIRECT,
)
from app.domain.task.models import Task, TaskMembership
from app.domain.task.repositories import TaskMembershipRepository
from app.domain.task.stages import list_stages
from app.domain.task.submission_state import (
    STAGE_NOT_SUBMITTED,
    refresh_completion_status,
    stage_states_of,
)
from app.domain.team.models import Team, TeamUserRelation

#: Days a claim of a task applied for has when the task names none.
_DAYS_WHEN_UNSET = 7
#: ``default_deadline`` was once kept in milliseconds; a value this large is one.
_MS_PER_DAY = 86_400_000


def starts_now(task: Task) -> bool:
    """Whether a claim of this task is approved the moment it is made."""
    return task.start_mode == START_DIRECT


def deadline_from(task: Task, approved_at: datetime) -> datetime | None:
    """The deadline a claim of this task gets when it is approved at
    ``approved_at``: the task's own on a task started directly, shared by
    everyone; on a task applied for, the task's 限时 days from approval."""
    if starts_now(task):
        return task.deadline
    days = task.default_deadline or 0
    if days >= _MS_PER_DAY:
        days = round(days / _MS_PER_DAY)
    return approved_at + timedelta(days=days or _DAYS_WHEN_UNSET)


def started_at():
    """When the claim (the ``TaskMembership`` row of the enclosing query)
    started: the moment its project was created, the project of the claim's
    team for this task (a person's claim: their personal team's). NULL while
    it has none — still waiting for approval, or approved and not started —
    so how long a claim has gone without moving counts from here, never from
    when it was applied for."""
    return (
        select(func.min(Project.created_at))
        .join(Team, Team.id == Project.team_id)
        .where(
            Project.external_task_id == TaskMembership.task_id,
            or_(
                and_(
                    TaskMembership.is_team.is_(True),
                    Project.team_id == TaskMembership.member_id,
                ),
                and_(
                    TaskMembership.is_team.is_(False),
                    Team.personal_owner_user_id == TaskMembership.member_id,
                ),
            ),
        )
        .correlate(TaskMembership)
        .scalar_subquery()
    )


async def started_at_of(
    session: AsyncSession, membership_ids: Sequence[int]
) -> dict[int, datetime]:
    """``started_at`` for each of these claims that has started."""
    if not membership_ids:
        return {}
    rows = await session.execute(
        select(TaskMembership.id, started_at()).where(
            TaskMembership.id.in_(membership_ids)
        )
    )
    return {int(mid): at for mid, at in rows if at is not None}


async def follow_the_task_deadline(
    session: AsyncSession, *, task: Task, before: datetime | None
) -> None:
    """On a task started directly everyone hands in by the task's deadline, so
    when it moves, the approved claims still holding the old one move with it.
    A claim whose deadline the publisher moved on its own keeps that one.
    Each claim that moved is derived again (``refresh_completion_status``): a
    claim the sweep failed at the old deadline is open again at a later one,
    and one past the new deadline with nothing in hand reads failed."""
    if not starts_now(task) or task.deadline == before:
        return
    moving = (
        await session.scalars(
            select(TaskMembership).where(
                TaskMembership.task_id == task.id,
                TaskMembership.deleted_at.is_(None),
                TaskMembership.approved == APPROVE_APPROVED,
                TaskMembership.deadline.is_not_distinct_from(before),
            )
        )
    ).all()
    for claim in moving:
        claim.deadline = task.deadline
        await refresh_completion_status(session, claim)


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
    live = _live_claims(task, user_membership, team_memberships)
    approved = [m for m in live if m.approved == 0]
    return (approved or live or [None])[0]


def _live_claims(
    task: Task,
    user_membership: TaskMembership | None,
    team_memberships: Sequence[TaskMembership],
) -> list[TaskMembership]:
    """The person's claims of the kind this task takes, rejected ones left out."""
    candidates = (
        [user_membership]
        if task.submitter_type == 0 and user_membership is not None
        else list(team_memberships)
        if task.submitter_type == 1
        else []
    )
    return [m for m in candidates if m.approved != 1]


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


async def hold_against_new_claims(session: AsyncSession, task_id: int) -> None:
    """Hold the task's row against new claims until commit (``FOR UPDATE``).

    Every new claim first reads the task's start mode under ``FOR KEY SHARE``
    (``read_mode_for_claim``), which this waits for and then blocks: a claim
    already being written is committed and counted before the caller reads
    the claims, and one arriving later reads the mode the caller leaves. Claims
    do not block one another on it (``FOR KEY SHARE`` is shared), nor do the
    place counts (``hold_places`` is ``FOR NO KEY UPDATE``)."""
    await session.execute(select(Task.id).where(Task.id == task_id).with_for_update())


async def read_mode_for_claim(session: AsyncSession, task: Task) -> None:
    """Read the task's start mode afresh under ``FOR KEY SHARE`` before a new
    claim is written, so the claim is made under the mode the task has when
    it commits: a change of mode (``hold_against_new_claims``) waits for it or
    is waited for."""
    await session.refresh(
        task,
        attribute_names=["start_mode"],
        # read + key_share is FOR KEY SHARE; key_share alone would be
        # FOR NO KEY UPDATE, which makes claims on one task wait for each other.
        with_for_update={"read": True, "key_share": True},
    )


def refuse_claim_after_deadline(task: Task, now: datetime) -> None:
    """Nobody makes a new claim once the task's deadline has passed: no
    application to a task applied for (its deadline is 申请截止), no start of
    a task started directly (its deadline is everyone's)."""
    if task.deadline is not None and task.deadline <= now:
        raise BadRequestError(say("taskClosedForStart"))


def refuse_start_after_deadline(
    task: Task, claim: TaskMembership, now: datetime
) -> None:
    """An approved claim is not started once its own deadline has passed (a
    project made then would only read failed). On a task started directly the
    task's deadline is everyone's, so it closes starting too; on a task
    applied for it closes applying only, and an application approved after it
    is started until the claim's own deadline."""
    if starts_now(task) and task.deadline is not None and task.deadline <= now:
        raise BadRequestError(say("taskClosedForStart"))
    if (
        claim.approved == APPROVE_APPROVED
        and claim.deadline is not None
        and claim.deadline <= now
    ):
        raise BadRequestError(say("claimDeadlinePassedForStart"))


async def has_live_claim(session: AsyncSession, task_id: int) -> bool:
    """Whether anyone holds a claim of this task that still counts: approved
    or waiting. Once one does, how the task starts is fixed: the claims made
    under one mode would read the other's rules (who sets the deadline, when
    the project is made)."""
    return bool(
        await session.scalar(
            select(
                exists().where(
                    TaskMembership.task_id == task_id,
                    TaskMembership.deleted_at.is_(None),
                    TaskMembership.approved != APPROVE_DISAPPROVED,
                )
            )
        )
    )


async def has_newer_claim(session: AsyncSession, claim: TaskMembership) -> bool:
    """Whether the same member applied again after this claim: a later row of
    theirs for the same task, not withdrawn. A rejected application with a
    newer one beside it is history; the publisher decides the newer one."""
    return bool(
        await session.scalar(
            select(
                exists().where(
                    TaskMembership.task_id == claim.task_id,
                    TaskMembership.member_id == claim.member_id,
                    TaskMembership.is_team.is_(claim.is_team),
                    TaskMembership.deleted_at.is_(None),
                    TaskMembership.id > claim.id,
                )
            )
        )
    )


@dataclass(frozen=True)
class ProjectDeadline:
    """When the project a claim opened has to hand in by."""

    at: datetime
    #: The claim's own deadline (set at approval); False when the claim has
    #: none yet and this is the challenge's closing time instead.
    mine: bool


async def project_claim(
    session: AsyncSession, *, task: Task, team_id: int | None
) -> TaskMembership | None:
    """The claim a project was opened for: a team claim is the project's
    team's, an individual one is the claim of the person whose personal team
    holds the project (``ProjectService.for_participation``). Approved before
    pending, never rejected — the same order as ``own_claim``."""
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
                TaskMembership.task_id == task.id,
                TaskMembership.deleted_at.is_(None),
                TaskMembership.approved != 1,
                or_(*whose),
            )
        )
    ).scalars()
    return min(claims, key=lambda m: m.approved != 0, default=None)


async def deadline_for_project(
    session: AsyncSession, *, task_id: int, team_id: int | None
) -> ProjectDeadline | None:
    """The overall deadline of the claim a project was opened for (see
    ``project_claim``), or the challenge's closing time while the claim has
    none. A staged task's stages carry their own due dates
    (``stages_for_project``); this stays the deadline of the whole."""
    task = await session.get(Task, task_id)
    if task is None:
        return None
    claim = await project_claim(session, task=task, team_id=team_id)
    if claim is not None and claim.approved == 0 and claim.deadline is not None:
        return ProjectDeadline(at=claim.deadline, mine=True)
    if task.deadline is not None:
        return ProjectDeadline(at=task.deadline, mine=False)
    return None


@dataclass(frozen=True)
class StageProgress:
    """One stage of the task a project was opened for, and where the project's
    claim stands on it (``submission_state`` STAGE_*)."""

    index: int
    name: str
    description: str
    due_at: datetime | None
    status: str


async def stages_for_project(
    session: AsyncSession, *, task_id: int, team_id: int | None
) -> list[StageProgress]:
    """The stages of the task a project was opened for, each with the
    project's claim's status on it; empty for a task without stages. Without
    an approved claim yet, every stage reads not handed in."""
    task = await session.get(Task, task_id)
    if task is None:
        return []
    stages = await list_stages(session, task.id)
    if not stages:
        return []
    claim = await project_claim(session, task=task, team_id=team_id)
    states = (
        await stage_states_of(session, claim)
        if claim is not None and claim.approved == 0
        else [STAGE_NOT_SUBMITTED] * len(stages)
    )
    return [
        StageProgress(
            index=stage.index,
            name=stage.name,
            description=stage.description,
            due_at=stage.due_at,
            status=state,
        )
        for stage, state in zip(stages, states, strict=False)
    ]
