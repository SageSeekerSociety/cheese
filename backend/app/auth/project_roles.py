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
3. the role this project records for them (``project_roles``): admin or one
   of the project's own roles. Only for someone who is in the project, through
   the team or a roster row; a row for someone who has since left grants
   nothing.
4. **member** — anyone else in the project, through the team or a roster row.

Nobody else sits anywhere, and the 出题人 of the task the project was opened
for and the admins of its space are nobody here unless they were seated as
its mentors.

An AI teammate is not a person and holds no role: it may do what a member may
by default, and never what manages people, the project or every channel
(:data:`AGENT_PERMISSIONS`), and also what only an AI teammate does
(``project_permissions.AGENT_ONLY``). It works for whoever asked it; what it reads for
them is decided where it reads (``app.domain.agent.audience``).

What only the owner does is not a permission; :func:`is_owner` answers it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from app.core.errors import ForbiddenError
from app.core.request_facts import recall, remember
from app.core.sentences import say
from app.domain.identity.services import IdentityService
from app.domain.membership.repositories import (
    MemberRepository,
    RoleRepository,
    role_key,
)
from app.domain.project.models import (
    Project,
    ProjectMember,
    ProjectMemberExclusion,
    ProjectRole,
    ProjectRoleDef,
)
from app.domain.project.repositories import ProjectRepository
from app.domain.project_permissions import (
    DEFAULT_GRANTS,
    PEOPLE_PERMISSIONS,
    RANK,
    Builtin,
    Permission,
    Seat,
    Template,
    with_locks,
)
from app.domain.team.models import Team, TeamUserRelation
from app.domain.team.protocol import TeamMemberRole
from app.domain.user.models import User
from app.domain.user.repositories import UserRepository

#: What an AI teammate may do in a project: what it does alone, and what a
#: person may short of managing.
AGENT_PERMISSIONS: frozenset[Permission] = frozenset(Permission) - {
    Permission.manage_members,
    Permission.change_roles,
    Permission.edit_settings,
    Permission.export_project,
    Permission.manage_channels,
    # Whether a document leaves the project is a person's call.
    Permission.share_documents,
}


@dataclass(frozen=True)
class RoleDef:
    """One role of a project, as everything that reads roles sees it."""

    #: ``admin``, ``member``, or the id of one of the project's own roles.
    key: str
    #: The project's own name for it. None for a built-in role, whose name is
    #: fixed, and for a templated role never renamed: whoever shows it writes
    #: the built-in's or the template's name.
    name: str | None
    builtin: Builtin | None
    template: Template | None
    permissions: frozenset[Permission]
    #: A built-in role whose permissions this project changed.
    changed: bool = False


@dataclass(frozen=True)
class Standing:
    """A person's place in a project and everything it lets them do."""

    seat: Seat
    permissions: frozenset[Permission]
    #: The key of the role they hold (``RoleDef.key``); ``owner`` for the owner.
    role: str = "member"
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


_OWNER_TABLES = frozenset(
    {"projects", "user", "team_user_relation", "project_members", "team"}
)


async def owner_of(session: AsyncSession, project: Project) -> str | None:
    """Who holds the project: its owner while they are still on its team, else
    the team's owner. A personal project's owner is its team's one person. A
    team that was disbanded has no owner left, and its archived projects stay
    their own owner's."""
    owner = project.owner_handle
    if project.team_id is None:
        return owner
    if session.autoflush:
        await session.flush()
    key = ("owner_of", project.id, owner, project.team_id)
    found, cached = recall(session, key)
    if found:
        return cached
    resolved = await _resolve_owner_of(session, project, owner)
    remember(session, key, resolved, tables=_OWNER_TABLES)
    return resolved


async def _resolve_owner_of(
    session: AsyncSession, project: Project, owner: str | None
) -> str | None:
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
    return await team_owner_handle(session, project.team_id) or owner


def _permissions(names: list[str] | None) -> frozenset[Permission]:
    return frozenset(
        Permission(p) for p in names or [] if p in Permission._value2member_map_
    )


def roles_from(rows: Sequence[ProjectRoleDef]) -> dict[str, RoleDef]:
    """A project's roles from its role rows: the two built in, each with this
    project's change if it made one, then its own in the order they were made."""
    changed = {r.builtin: r for r in rows if r.builtin is not None}
    out: dict[str, RoleDef] = {}
    for builtin in Builtin:
        row = changed.get(builtin.value)
        granted = _permissions(row.permissions) if row else DEFAULT_GRANTS[builtin]
        out[builtin.value] = RoleDef(
            key=builtin.value,
            name=None,
            builtin=builtin,
            template=None,
            permissions=with_locks(builtin is Builtin.admin, granted),
            changed=row is not None,
        )
    for row in rows:
        if row.builtin is not None:
            continue
        out[str(row.id)] = RoleDef(
            key=str(row.id),
            name=row.name,
            builtin=None,
            template=Template(row.template)
            if row.template in Template._value2member_map_
            else None,
            permissions=with_locks(False, _permissions(row.permissions)),
        )
    return out


async def project_roles(
    session: AsyncSession, project_id: uuid.UUID
) -> dict[str, RoleDef]:
    """Every role of this project, by key."""
    return roles_from(await RoleRepository(session).defs(project_id))


def _held(roles: dict[str, RoleDef], recorded: str | None) -> Standing:
    """Where someone in the project sits by the role recorded for them."""
    role = roles.get(recorded or "") or roles[Builtin.member.value]
    seat = Seat.admin if role.builtin is Builtin.admin else Seat.member
    return Standing(seat=seat, permissions=role.permissions, role=role.key)


def _team_admin(roles: dict[str, RoleDef]) -> Standing:
    admin = roles[Builtin.admin.value]
    return Standing(
        seat=Seat.admin, permissions=admin.permissions, from_team=True, role=admin.key
    )


async def standing(
    session: AsyncSession, project_id: uuid.UUID, handle: str | None
) -> Standing | None:
    """Where ``handle`` sits in the project, or None when they are not in it.

    One request asks this several times with the same arguments — room
    admission, the role gate and the route's own permission — and each answer
    costs a handful of statements. The first is remembered on the session
    (`core.request_facts`); a write in that session forgets it, so an answer
    never outlives the rows it was read from. ``None`` is remembered too:
    "not in this project" is asked as often as the positive answer, and adding
    someone is a write, which forgets it.
    """
    if not handle:
        return None
    key = ("standing", project_id, handle)
    found, answer = recall(session, key)
    if found:
        return answer
    answer = await _standing(session, project_id, handle)
    remember(session, key, answer)
    return answer


async def _standing(
    session: AsyncSession, project_id: uuid.UUID, handle: str
) -> Standing | None:
    """Read where ``handle`` sits, from the rows."""
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
    everything = PEOPLE_PERMISSIONS
    if handle == await owner_of(session, project):
        return Standing(seat=Seat.owner, permissions=everything, role="owner")
    user = await UserRepository(session).get_by_username(handle)
    members = MemberRepository(session)
    excluded = await members.is_excluded(project_id=project_id, user_handle=handle)
    team_role = (
        await _team_role(session, project.team_id, user.id)
        if user is not None
        else None
    )
    if team_role == TeamMemberRole.OWNER:
        return Standing(
            seat=Seat.owner, permissions=everything, from_team=True, role="owner"
        )
    roles = await project_roles(session, project_id)
    if team_role == TeamMemberRole.ADMIN and not excluded:
        return _team_admin(roles)
    on_roster = await members.get(project_id=project_id, user_handle=handle) is not None
    if not on_roster and (team_role is None or excluded):
        return None
    recorded = await RoleRepository(session).role_of(
        project_id=project_id, user_handle=handle
    )
    return _held(roles, recorded)


async def seats(
    session: AsyncSession, project_id: uuid.UUID, handles: list[str]
) -> dict[str, Standing]:
    """Where each of ``handles``, all people already in the project, sits: the
    answer :func:`standing` gives one by one, read in a fixed number of queries
    however long the roster is."""
    project = await ProjectRepository(session).get(project_id)
    if project is None or not handles:
        return {}
    owner = await owner_of(session, project)
    roles = await project_roles(session, project_id)
    recorded = await RoleRepository(session).roles(project_id)
    excluded = await MemberRepository(session).excluded_handles(project_id)
    team_roles: dict[str, int] = {}
    if project.team_id is not None:
        found_tr, cached_tr = (
            recall(session, ("team_roles_by_handle", project.team_id))
            if isinstance(session, AsyncSession)
            else (False, None)
        )
        if found_tr and cached_tr is not None:
            team_roles = {h: cached_tr[h] for h in handles if h in cached_tr}
        else:
            rows = await session.execute(
                select(User.username, TeamUserRelation.role)
                .join(TeamUserRelation, TeamUserRelation.user_id == User.id)
                .where(
                    TeamUserRelation.team_id == project.team_id,
                    TeamUserRelation.deleted_at.is_(None),
                    User.username.in_(handles),
                )
            )
            team_roles = {name: role for name, role in rows.all()}
    everything = PEOPLE_PERMISSIONS
    out: dict[str, Standing] = {}
    for handle in handles:
        team_role = team_roles.get(handle)
        if handle == owner:
            out[handle] = Standing(
                seat=Seat.owner, permissions=everything, role="owner"
            )
        elif team_role == TeamMemberRole.OWNER:
            out[handle] = Standing(
                seat=Seat.owner, permissions=everything, from_team=True, role="owner"
            )
        elif team_role == TeamMemberRole.ADMIN and handle not in excluded:
            out[handle] = _team_admin(roles)
        else:
            out[handle] = _held(roles, recorded.get(handle))
    return out


class ProjectSeats(Protocol):
    """What :func:`standings_in` reads off a project row."""

    id: Mapped[uuid.UUID]
    owner_handle: Mapped[str | None]
    team_id: Mapped[int]


async def standings_in(
    session: AsyncSession, projects: Sequence[ProjectSeats], handle: str | None
) -> dict[uuid.UUID, Standing | None]:
    """Where ``handle`` sits in each of ``projects``: :func:`standing` for a
    list of projects, read in a fixed number of queries however many there are."""
    if not handle or not projects:
        return {p.id: None for p in projects}
    ids = [p.id for p in projects]
    if await IdentityService(session).is_agent(handle):
        seated = set(
            (
                await session.scalars(
                    select(ProjectMember.project_id).where(
                        ProjectMember.user_handle == handle,
                        ProjectMember.project_id.in_(ids),
                    )
                )
            ).all()
        )
        return {
            p.id: Standing(seat=Seat.member, permissions=AGENT_PERMISSIONS)
            if p.id in seated
            else None
            for p in projects
        }
    user = await UserRepository(session).get_by_username(handle)
    team_roles: dict[int, int] = {}
    if user is not None:
        team_ids = {p.team_id for p in projects if p.team_id is not None}
        rows = await session.execute(
            select(TeamUserRelation.team_id, TeamUserRelation.role).where(
                TeamUserRelation.user_id == user.id,
                TeamUserRelation.team_id.in_(team_ids),
                TeamUserRelation.deleted_at.is_(None),
            )
        )
        team_roles = dict(rows.tuples().all())
    on_roster = set(
        (
            await session.scalars(
                select(ProjectMember.project_id).where(
                    ProjectMember.user_handle == handle,
                    ProjectMember.project_id.in_(ids),
                )
            )
        ).all()
    )
    excluded = set(
        (
            await session.scalars(
                select(ProjectMemberExclusion.project_id).where(
                    ProjectMemberExclusion.user_handle == handle,
                    ProjectMemberExclusion.project_id.in_(ids),
                )
            )
        ).all()
    )
    recorded = {
        project_id: role_key(role, role_id)
        for project_id, role, role_id in (
            await session.execute(
                select(
                    ProjectRole.project_id, ProjectRole.role, ProjectRole.role_id
                ).where(
                    ProjectRole.user_handle == handle,
                    ProjectRole.project_id.in_(ids),
                )
            )
        ).tuples()
    }
    defs = await RoleRepository(session).defs_of(ids)
    everything = PEOPLE_PERMISSIONS
    out: dict[uuid.UUID, Standing | None] = {}
    for p in projects:
        team_role = team_roles.get(p.team_id) if p.team_id is not None else None
        if p.owner_handle == handle and not (
            p.team_id is None or team_role is not None or p.id in on_roster
        ):
            # An owner who has left: whether they still hold it depends on
            # whether the team has an owner left (:func:`owner_of`).
            out[p.id] = await standing(session, p.id, handle)
            continue
        if p.owner_handle == handle:
            out[p.id] = Standing(seat=Seat.owner, permissions=everything, role="owner")
            continue
        if team_role == TeamMemberRole.OWNER:
            out[p.id] = Standing(
                seat=Seat.owner, permissions=everything, from_team=True, role="owner"
            )
            continue
        roles = roles_from(defs.get(p.id, []))
        if team_role == TeamMemberRole.ADMIN and p.id not in excluded:
            out[p.id] = _team_admin(roles)
            continue
        if p.id not in on_roster and (team_role is None or p.id in excluded):
            out[p.id] = None
            continue
        out[p.id] = _held(roles, recorded.get(p.id))
    return out


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

    A person manages only those strictly below them: an admin manages everyone
    holding another role, not another admin; the owner manages every admin. An admin
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
