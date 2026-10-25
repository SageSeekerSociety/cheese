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

from app.core.errors import ForbiddenError
from app.core.sentences import say
from app.domain.task.models import Task, TaskMembership
from app.domain.task.repositories import TaskMembershipRepository
from app.domain.task.stages import list_stages
from app.domain.task.submission_state import STAGE_NOT_SUBMITTED, stage_states_of
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


async def refuse_unless_project_follows_claim(
    session: AsyncSession, *, task: Task, user_id: int, team_id: int | None
) -> None:
    """A project made from a task belongs to the team of the claim it comes
    from: a person's claim puts it in their own name (their personal team, which
    is also where a project with no team goes), a team's claim in that team.
    Without a live claim there is no project to make; any other team is refused
    by name, so the person knows where to make it."""
    repo = TaskMembershipRepository(session=session)
    live = _live_claims(
        task,
        await repo.get_user_membership(task_id=task.id, user_id=user_id),
        await repo.list_team_memberships_for_user(task_id=task.id, user_id=user_id),
    )
    if not live:
        raise ForbiddenError(say("challengeProjectClaimFirst"))
    team = (await session.get(Team, team_id)) if team_id is not None else None
    if any(
        claim.member_id == team_id
        if claim.is_team
        else team_id is None
        or (team is not None and team.personal_owner_user_id == user_id)
        for claim in live
    ):
        return
    # Named after the claim the person stands on: approved before pending.
    claim = min(live, key=lambda m: m.approved != 0)
    if claim.is_team:
        claimant = await session.get(Team, claim.member_id)
        raise ForbiddenError(
            say(
                "challengeProjectInClaimTeam",
                team=claimant.name if claimant is not None else "",
            )
        )
    raise ForbiddenError(say("challengeProjectInOwnName"))


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
