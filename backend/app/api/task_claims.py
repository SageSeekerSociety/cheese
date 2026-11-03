"""A claim's project: made when the claimant starts the task, found again after.

开始 is 用这道题新建项目. A claim of a task started directly is approved the
moment it is made, and the same click starts it; a claim of a task applied for
is approved by the publisher, and the claimant starts it afterwards with
开始做这道题. Either way the project is made then, under the claim's team (a
person's claim in their own name, a team's claim in that team), with the
mentors the claimant agrees to at that moment, and never for a claim still
waiting. The claim routes start it here; the task page reads it here for
继续做.
"""

from datetime import UTC, datetime

from app.core.errors import NotFoundError
from app.domain.agent_instance.adapters import default_teammate
from app.domain.living_doc.overview import seed_overview
from app.domain.membership.services import seat_mentors
from app.domain.project.models import Project
from app.domain.project.services import ProjectService
from app.domain.task.adapters import TaskTerms, as_brief, as_claim
from app.domain.task.claims import refuse_start_after_deadline
from app.domain.task.inputs import APPROVE_APPROVED
from app.domain.task.mentors import mentors_of, require_consent
from app.domain.task.models import Task, TaskMembership
from app.domain.team.services import team_service
from app.domain.usage.adapters import ComputeCredits
from app.domain.user.services import user_by_id


def _projects(db) -> ProjectService:
    return ProjectService(
        db,
        default_teammate=default_teammate,
        compute_credits=ComputeCredits(db),
        overview=seed_overview,
        task_terms=TaskTerms(db),
        seat_mentors=seat_mentors,
    )


def _owner_id(membership: TaskMembership, acting_user_id: int) -> int:
    """Whose name the project is made in: the person who claimed, or for a
    team's claim the member starting it (only a member of the team may)."""
    return membership.member_id if not membership.is_team else acting_user_id


async def _open_claim_project(
    db, *, task: Task, membership: TaskMembership, acting_user_id: int
) -> Project:
    """The approved claim's project, made now if it has none, and the task's
    resources released to it.

    What the caller wrote (the claim, the place it took, the mentors agreed
    to) is committed
    first, with the claimant's personal team for a person's claim: neither
    the task's row nor a team being made is held while the repository is made,
    so a second request for the same claim is told at once that its project is
    being made (``ClaimInProgressError``) rather than waiting behind it."""
    owner = await user_by_id(db, _owner_id(membership, acting_user_id))
    if owner is None:
        raise NotFoundError("Participant user not found")
    if not membership.is_team:
        await team_service(db).ensure_personal_team(owner.id)
    await db.commit()
    projects = _projects(db)
    # The mentors the claimant agreed to when starting, not the task's list
    # now: someone added to the task since has no consent here.
    project = await projects.for_participation(
        task=as_brief(task, mentors=tuple(membership.agreed_mentors or ())),
        claim=as_claim(membership),
        owner_handle=owner.username,
    )
    await projects.activate_participation(
        task=as_brief(task), claim=as_claim(membership)
    )
    return project


async def start_claim(
    db,
    *,
    task: Task,
    membership: TaskMembership,
    acting_user_id: int,
    agreed_mentors: bool,
) -> Project:
    """开始 an approved claim: the project it already has (继续做, asking
    nothing), or a new one. A new one seats the task's mentors only with the
    claimant's agreement now, and those are the mentors recorded on the claim;
    a task with mentors is not started without it."""
    project = await _claim_project(
        db, task=task, membership=membership, user_id=acting_user_id
    )
    if project is None:
        refuse_start_after_deadline(task, membership, datetime.now(UTC))
        await require_consent(db, task=task, agreed=agreed_mentors)
        membership.agreed_mentors = await mentors_of(db, task.id)
    return await _open_claim_project(
        db, task=task, membership=membership, acting_user_id=acting_user_id
    )


async def release_to_claim_project(
    db, *, task: Task, membership: TaskMembership
) -> None:
    """On approval, the task's resources go to the project the claim already
    has, if any: one an earlier release made while the claim waited. Approval
    makes no project; the claimant starts it."""
    await _projects(db).activate_participation(
        task=as_brief(task), claim=as_claim(membership)
    )


async def _claim_project(
    db, *, task: Task, membership: TaskMembership, user_id: int
) -> Project | None:
    team_id = (
        membership.member_id
        if membership.is_team
        else (await team_service(db).ensure_personal_team(user_id)).id
    )
    return await _projects(db).claimed_project(task.id, team_id)


async def project_of_claim(
    db, *, task: Task, membership: TaskMembership | None, user_id: int
) -> Project | None:
    """The project a person's approved claim of this task already has: what
    继续做 opens. Looked up, never made. A claim still waiting has none to
    continue, even one an earlier release gave a project while it waited: the
    page shows it waiting until it is approved."""
    if membership is None or membership.approved != APPROVE_APPROVED:
        return None
    return await _claim_project(db, task=task, membership=membership, user_id=user_id)
