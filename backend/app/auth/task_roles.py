"""Who may do what to a problem (题目) published in a space.

A problem has no seats of its own: what a person may do follows from who they
are to it.

- **Teaching it** is for its setter and for whoever teaches the space it is in
  (``app.domain.space.permissions``, ``teach``). They edit and delete it, grade
  what is handed in, and manage who takes part.
- **Approving it**, taking it up on the board, is for whoever teaches the space,
  never its own setter.
- **Seeing it** depends on three things. An unapproved problem is seen only by
  those who teach it. Otherwise the person must see it on the board: in the
  space, and on its access list when the problem has one
  (``TaskVisibilityService``). Finally, within the space's limit on how many
  problems each setter shows; those who teach are not held to that limit.
- **Taking it up** is for anyone who sees it on the board. Whether it is
  approved yet is the caller's to say, with its own sentence.
- **Downloading its files** is for those who teach it, and for anyone whose
  claim on it was approved.

It lives beside ``project_roles`` rather than in the task domain because it
spans the problem, its space and its claimants' teams, and the space domain
already reads the task domain. A route declares what it needs
(``app.api.write_access.Can``) and ``app.auth.can`` asks :data:`TASK`. What
depends on the claim a request names — reading its submissions, handing work
in, withdrawing it — is asked of :data:`CLAIM` by the handler once it holds the
claim (``app.auth.can.require``). What a person cannot see does not exist for
them: ``TASK.sees`` is the question behind every 404.
"""

import enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.policy import Policy
from app.domain.space import permissions as space
from app.domain.space.repositories import SpaceRepository
from app.domain.task.claims import claim_of
from app.domain.task.inputs import APPROVE_APPROVED, APPROVE_NONE
from app.domain.task.models import Task, TaskMembership
from app.domain.task.repositories import TaskRepository
from app.domain.task.visibility_service import TaskVisibilityService
from app.domain.team.models import TeamUserRelation


class Permission(enum.StrEnum):
    #: Read the problem and what hangs off it: its files list, versions,
    #: progress and what a project made from it inherits.
    view = "view"
    #: Take it up, alone or with a team.
    take_up = "take_up"
    #: Edit or delete it, grade submissions, manage and enrol participants.
    teach = "teach"
    #: Pass it onto the board, or send it back.
    approve = "approve"
    #: Download its files.
    download = "download"


async def teaches(session: AsyncSession, task: Task, user_id: int | None) -> bool:
    """Its setter, or someone who teaches the space it is in."""
    if user_id is None:
        return False
    if task.creator_id is not None and task.creator_id == user_id:
        return True
    return await space.SPACE.allows(
        session, task.space_id, user_id, space.Permission.teach
    )


async def space_teachers(session: AsyncSession, space_id: int | None) -> set[int]:
    """Everyone who teaches the space a problem is in, by user id."""
    return await space.managers(session, space_id)


async def _on_the_board(session: AsyncSession, task: Task, user_id: int) -> bool:
    """Seen on the board: visible to them, and within the space's per-setter
    limit unless they teach it."""
    if not await TaskVisibilityService(session=session).can_view_task(
        task=task, user_id=user_id
    ):
        return False
    if await teaches(session, task, user_id):
        return True
    found = await SpaceRepository(session=session).get_by_id(task.space_id)
    if found is None:
        return False
    return await TaskRepository(session=session).is_task_visible_for_space_limit(
        task=task, visible_task_limit=found.visible_task_limit
    )


async def readable(session: AsyncSession, task: Task, user_id: int) -> bool:
    if task.approved == APPROVE_NONE and task.ended_at is None:
        if not await teaches(session, task, user_id):
            return False
    return await _on_the_board(session, task, user_id)


class TaskPolicy(Policy[Task, Permission]):
    kind = "task"
    path_param = "taskId"
    permissions = Permission

    async def load(self, session: AsyncSession, id: int) -> Task | None:
        return await TaskRepository(session=session).get_by_id(id)

    async def may(
        self, session: AsyncSession, obj: Task, user_id: int, permission: Permission
    ) -> bool:
        match permission:
            case Permission.view:
                return await readable(session, obj, user_id)
            case Permission.take_up:
                return await _on_the_board(session, obj, user_id)
            case Permission.teach:
                return await teaches(session, obj, user_id)
            case Permission.approve:
                return await space.SPACE.allows(
                    session, obj.space_id, user_id, space.Permission.teach
                )
            case Permission.download:
                if await teaches(session, obj, user_id):
                    return True
                claim = await claim_of(session, task=obj, user_id=user_id)
                return claim is not None and claim.approved == APPROVE_APPROVED

    async def sees(self, session: AsyncSession, obj: Task, user_id: int) -> bool:
        return await readable(session, obj, user_id)


TASK = TaskPolicy()


async def _in_the_claim(
    session: AsyncSession, membership: TaskMembership, user_id: int
) -> bool:
    """Whether the person made this claim, or is on the team that did."""
    if not membership.is_team:
        return membership.member_id == user_id
    found = await session.scalar(
        select(TeamUserRelation.id).where(
            TeamUserRelation.team_id == membership.member_id,
            TeamUserRelation.user_id == user_id,
            TeamUserRelation.deleted_at.is_(None),
        )
    )
    return found is not None


class ClaimPermission(enum.StrEnum):
    #: Read the claim's submissions and reviews.
    read = "read"
    #: Hand in work on it, or change what was handed in.
    hand_in = "hand_in"
    #: Withdraw it.
    withdraw = "withdraw"


class ClaimPolicy(Policy[TaskMembership, ClaimPermission]):
    """A claim on a problem — a person's, or a team's.

    Whoever made it reads it and hands work in on it (anyone on the team, for
    a team's claim); those who teach the problem read every claim. Withdrawing
    one is for the person who made it alone, or those who teach the problem: a
    team's claim is withdrawn by its teachers, not by one of its members."""

    kind = "participant"
    path_param = "participantId"
    permissions = ClaimPermission

    async def load(self, session: AsyncSession, id: int) -> TaskMembership | None:
        claim = await session.get(TaskMembership, id)
        return None if claim is None or claim.deleted_at is not None else claim

    async def may(
        self,
        session: AsyncSession,
        obj: TaskMembership,
        user_id: int,
        permission: ClaimPermission,
    ) -> bool:
        task = await session.get(Task, obj.task_id)
        teacher = task is not None and await teaches(session, task, user_id)
        match permission:
            case ClaimPermission.read:
                return teacher or await _in_the_claim(session, obj, user_id)
            case ClaimPermission.hand_in:
                return await _in_the_claim(session, obj, user_id)
            case ClaimPermission.withdraw:
                own = not obj.is_team and obj.member_id == user_id
                return own or teacher


CLAIM = ClaimPolicy()
