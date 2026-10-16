"""Who someone is in a project, and whether that lets them do a thing (#3830).

One answer for every route: :func:`seat_of` says where a person sits, and
:func:`may` whether that seat may do one :class:`Permission`. A route asks
``may`` (or :func:`require`) and nothing else; it does not look at owners,
team admins or roster rows itself.

Where a person sits, in this order:

1. **owner** — the project's ``owner_handle``, or the owner of the team the
   project belongs to, who stands above every project of the team. When the
   project's owner has left a shared team, the team's owner holds the project.
2. **admin** — an admin of that team, unless they left this project.
3. the role this project records for them (``project_roles``): admin, member
   or mentor. Only for someone who is in the project, through the team or a
   roster row; a row for someone who has since left grants nothing.
4. **member** — anyone else in the project, through the team or a roster row.

Nobody else sits anywhere, and the 出题人 of the task the project was opened
for and the admins of its space are nobody here unless they were made mentors.

An AI teammate is not a person and holds no role: it may do what a member may
by default, and never what manages people, the project or every channel
(:data:`AGENT_PERMISSIONS`). It works for whoever asked it; what it reads for
them is decided where it reads (``app.domain.agent.audience``).

What only the owner does is not a permission; :func:`is_owner` answers it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError
from app.core.sentences import say
from app.domain.identity.services import IdentityService
from app.domain.membership.repositories import MemberRepository, RoleRepository
from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository
from app.domain.project_permissions import (
    DEFAULT_GRANTS,
    RANK,
    Permission,
    Role,
    Seat,
    with_locks,
)
from app.domain.team.models import Team, TeamUserRelation
from app.domain.team.protocol import TeamMemberRole
from app.domain.user.models import User
from app.domain.user.repositories import UserRepository

#: What an AI teammate may do in a project.
AGENT_PERMISSIONS: frozenset[Permission] = frozenset(Permission) - {
    Permission.manage_members,
    Permission.change_roles,
    Permission.edit_settings,
    Permission.export_project,
    Permission.manage_channels,
}


@dataclass(frozen=True)
class Standing:
    """A person's place in a project and everything it lets them do."""

    seat: Seat
    permissions: frozenset[Permission]
    #: Their admin rank comes from the team (team owner or admin), so this
    #: project cannot change it.
    from_team: bool = False


async def _team_role(
    session: AsyncSession, team_id: int | None, user_id: int
) -> int | None:
    if team_id is None:
        return None
    return await session.scalar(
        select(TeamUserRelation.role).where(
            TeamUserRelation.team_id == team_id,
            TeamUserRelation.user_id == user_id,
            TeamUserRelation.deleted_at.is_(None),
        )
    )


async def team_owner_handle(session: AsyncSession, team_id: int) -> str | None:
    return await session.scalar(
        select(User.username)
        .join(TeamUserRelation, TeamUserRelation.user_id == User.id)
        .where(
            TeamUserRelation.team_id == team_id,
            TeamUserRelation.role == TeamMemberRole.OWNER,
            TeamUserRelation.deleted_at.is_(None),
        )
    )


async def is_personal(session: AsyncSession, project: Project) -> bool:
    """Whether the project sits in one person's own team."""
    return (
        await session.scalar(
            select(Team.personal_owner_user_id).where(Team.id == project.team_id)
        )
    ) is not None


async def owner_of(session: AsyncSession, project: Project) -> str | None:
    """Who holds the project: its owner while they are still on its team, else
    the team's owner. A personal project's owner is its team's one person."""
    owner = project.owner_handle
    if project.team_id is None:
        return owner
    if owner:
        user = await UserRepository(session).get_by_username(owner)
        if (
            user is not None
            and await _team_role(session, project.team_id, user.id) is not None
        ):
            return owner
        # An owner who is no longer on the team but was let in by a roster row
        # (a personal project's owner always is on its team) still holds it.
        if await MemberRepository(session).get(
            project_id=project.id, user_handle=owner
        ):
            return owner
    return await team_owner_handle(session, project.team_id)


async def _grants(
    session: AsyncSession, project_id: uuid.UUID
) -> dict[Role, frozenset[Permission]]:
    changed = await RoleRepository(session).grants(project_id)
    out: dict[Role, frozenset[Permission]] = {}
    for role in Role:
        if role.value in changed:
            granted = frozenset(
                Permission(p)
                for p in changed[role.value]
                if p in Permission._value2member_map_
            )
        else:
            granted = DEFAULT_GRANTS[role]
        out[role] = with_locks(role, granted)
    return out


async def role_grants(
    session: AsyncSession, project_id: uuid.UUID
) -> dict[Role, frozenset[Permission]]:
    """What each role may do in this project now."""
    return await _grants(session, project_id)


async def standing(
    session: AsyncSession, project_id: uuid.UUID, handle: str | None
) -> Standing | None:
    """Where ``handle`` sits in the project, or None when they are not in it."""
    if not handle:
        return None
    project = await ProjectRepository(session).get(project_id)
    if project is None:
        return None
    if await IdentityService(session).is_agent(handle):
        on_roster = await MemberRepository(session).get(
            project_id=project_id, user_handle=handle
        )
        if on_roster is None:
            return None
        return Standing(seat=Seat.member, permissions=AGENT_PERMISSIONS)
    everything = frozenset(Permission)
    if handle == await owner_of(session, project):
        return Standing(seat=Seat.owner, permissions=everything)
    user = await UserRepository(session).get_by_username(handle)
    members = MemberRepository(session)
    excluded = await members.is_excluded(project_id=project_id, user_handle=handle)
    team_role = (
        await _team_role(session, project.team_id, user.id)
        if user is not None
        else None
    )
    if team_role == TeamMemberRole.OWNER:
        return Standing(seat=Seat.owner, permissions=everything, from_team=True)
    grants = await _grants(session, project_id)
    if team_role == TeamMemberRole.ADMIN and not excluded:
        return Standing(seat=Seat.admin, permissions=grants[Role.admin], from_team=True)
    on_roster = await members.get(project_id=project_id, user_handle=handle) is not None
    if not on_roster and (team_role is None or excluded):
        return None
    recorded = await RoleRepository(session).role_of(
        project_id=project_id, user_handle=handle
    )
    role = Role(recorded) if recorded in Role._value2member_map_ else Role.member
    return Standing(seat=Seat(role.value), permissions=grants[role])


async def may(
    session: AsyncSession,
    project_id: uuid.UUID,
    handle: str | None,
    permission: Permission,
) -> bool:
    found = await standing(session, project_id, handle)
    return found is not None and permission in found.permissions


async def require(
    session: AsyncSession,
    project_id: uuid.UUID,
    handle: str | None,
    permission: Permission,
) -> None:
    """Refuse unless ``handle`` may do ``permission`` here."""
    if not await may(session, project_id, handle, permission):
        raise ForbiddenError(say("projectPermissionDenied"))


async def is_owner(
    session: AsyncSession, project_id: uuid.UUID, handle: str | None
) -> bool:
    found = await standing(session, project_id, handle)
    return found is not None and found.seat is Seat.owner


async def require_owner(
    session: AsyncSession, project_id: uuid.UUID, handle: str | None
) -> None:
    if not await is_owner(session, project_id, handle):
        raise ForbiddenError(say("projectOwnerOnly"))


async def require_above(
    session: AsyncSession, project_id: uuid.UUID, actor: str | None, target: str
) -> None:
    """Refuse unless ``actor`` sits above ``target`` here.

    A person manages only those strictly below them: an admin manages members
    and mentors, not another admin; the owner manages every admin. An admin
    rank that comes from the team is the team's to change, so only the team's
    owner moves it here. Someone not in the project is below everyone in it.
    """
    mine = await standing(session, project_id, actor)
    theirs = await standing(session, project_id, target)
    if mine is None:
        raise ForbiddenError(say("projectPermissionDenied"))
    if theirs is None:
        return
    if theirs.from_team and not (mine.seat is Seat.owner and mine.from_team):
        raise ForbiddenError(say("roleFromTeam"))
    if RANK[mine.seat] <= RANK[theirs.seat]:
        raise ForbiddenError(say("roleOnlyBelow"))
