"""A claim's project: opened once the claim is approved, found again after.

A claim of a task started directly is approved the moment it is made; a claim
of a task applied for is approved by the publisher. Either way its project
opens then, under the claim's team (a person's claim in their own name, a
team's claim in that team), and never for a claim still waiting. The claim
routes and the two approval routes open it here; the task page reads it here
for 继续做.
"""

from app.core.errors import NotFoundError
from app.domain.agent_instance.adapters import default_teammate
from app.domain.living_doc.overview import seed_overview
from app.domain.membership.services import seat_mentors
from app.domain.project.models import Project
from app.domain.project.services import ProjectService
from app.domain.task.adapters import TaskTerms, as_brief, as_claim
from app.domain.task.mentors import mentors_of
from app.domain.task.models import Task, TaskMembership
from app.domain.team.protocol import TeamMemberRole
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


async def _owner_id(db, membership: TaskMembership, acting_user_id: int) -> int:
    """Whose name the project is made in: the person who claimed, or for a
    team's claim the member acting now, or the team's owner when the one acting
    is not in the team (the publisher approving it)."""
    if not membership.is_team:
        return membership.member_id
    members = await team_service(db).get_team_members(membership.member_id)
    if any(member.user_id == acting_user_id for member in members):
        return acting_user_id
    owner = next((m.user_id for m in members if m.role == TeamMemberRole.OWNER), None)
    if owner is None:
        raise NotFoundError("Team owner not found")
    return owner


async def open_claim_project(
    db, *, task: Task, membership: TaskMembership, acting_user_id: int
) -> Project:
    """The approved claim's project, made now if it has none, and the task's
    resources released to it.

    What the caller wrote (the approval, and the place it took) is committed
    first, with the claimant's personal team for a person's claim: neither
    the task's row nor a team being made is held while the repository is made,
    so a second request for the same claim is told at once that its project is
    being made (``ClaimInProgressError``) rather than waiting behind it."""
    owner = await user_by_id(db, await _owner_id(db, membership, acting_user_id))
    if owner is None:
        raise NotFoundError("Participant user not found")
    if not membership.is_team:
        await team_service(db).ensure_personal_team(owner.id)
    await db.commit()
    projects = _projects(db)
    project = await projects.for_participation(
        task=as_brief(task, mentors=tuple(await mentors_of(db, task.id))),
        claim=as_claim(membership),
        owner_handle=owner.username,
    )
    await projects.activate_participation(
        task=as_brief(task), claim=as_claim(membership)
    )
    return project


async def project_of_claim(
    db, *, task: Task, membership: TaskMembership | None, user_id: int
) -> Project | None:
    """The project a person's claim of this task already has, if any: what
    继续做 opens. Looked up, never made."""
    if membership is None:
        return None
    team_id = (
        membership.member_id
        if membership.is_team
        else (await team_service(db).ensure_personal_team(user_id)).id
    )
    return await _projects(db).claimed_project(task.id, team_id)
