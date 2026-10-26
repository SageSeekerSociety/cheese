"""Who someone is on a team, and what that lets them do.

A team has one owner, admins and members. Each seat holds a fixed set of the
permissions below; the owner holds all of them. :func:`seat_of` says where a
person sits, :func:`may` whether that seat holds a permission, and
:func:`may_remove` whether it reaches someone else's seat: a person manages only
those strictly below their own (:data:`RANK`). Every check of a team role asks
these, whether from a route (``app.auth.can``) or from a service acting on a
team's machines, documents or recruitment posts; the team's services keep only
the team's own rules (a personal team is never disbanded, the owner changes
hands only by transfer).
"""

import enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.team.models import Team, TeamUserRelation
from app.domain.team.protocol import TeamMemberRole


class Seat(enum.StrEnum):
    owner = "owner"
    admin = "admin"
    member = "member"


class Permission(enum.StrEnum):
    #: See who is on the team and what role each holds.
    view_members = "view_members"
    #: The team's name, intro, avatar and listing, and its join link.
    edit_settings = "edit_settings"
    #: Bring people in as members — adding, inviting, answering join requests —
    #: and take out those below one's seat.
    manage_members = "manage_members"
    #: Make someone an admin or a member, whether they are on the team already
    #: or are being added or invited.
    change_roles = "change_roles"
    #: The team's devices, model connections and Claude sign-ins: enrol,
    #: rename, share and remove them.
    manage_machines = "manage_machines"
    #: Move or delete any of the team's own documents, not only one's own.
    manage_documents = "manage_documents"
    #: Post and take down the team's recruitment posts.
    recruit = "recruit"
    #: Disband the team.
    disband = "disband"
    #: Hand the team to another member.
    transfer = "transfer"


GRANTS: dict[Seat, frozenset[Permission]] = {
    Seat.owner: frozenset(Permission),
    Seat.admin: frozenset(
        {
            Permission.view_members,
            Permission.edit_settings,
            Permission.manage_members,
            Permission.manage_machines,
            Permission.manage_documents,
            Permission.recruit,
        }
    ),
    Seat.member: frozenset({Permission.view_members}),
}

#: Higher first. A person manages only those strictly below their own seat.
RANK: dict[Seat, int] = {Seat.owner: 3, Seat.admin: 2, Seat.member: 1}

_SEATS = {
    TeamMemberRole.OWNER: Seat.owner,
    TeamMemberRole.ADMIN: Seat.admin,
    TeamMemberRole.MEMBER: Seat.member,
}


def seat_named(role: int | None) -> Seat | None:
    """The seat a stored team role stands for; ``None`` for no row."""
    return _SEATS.get(role) if role is not None else None


async def seat_of(session: AsyncSession, team_id: int, user_id: int) -> Seat | None:
    """Where the person sits on the team; ``None`` when they are not on it."""
    role = await session.scalar(
        select(TeamUserRelation.role).where(
            TeamUserRelation.team_id == team_id,
            TeamUserRelation.user_id == user_id,
            TeamUserRelation.deleted_at.is_(None),
        )
    )
    return seat_named(role)


async def may(
    session: AsyncSession, team_id: int, user_id: int, permission: Permission
) -> bool:
    seat = await seat_of(session, team_id, user_id)
    return seat is not None and permission in GRANTS[seat]


async def sees(session: AsyncSession, team_id: int, user_id: int) -> bool:
    """Whether the team exists for this person: they are on it, or it is a
    public shared team. Anyone else is answered as if it did not exist."""
    team = await session.get(Team, team_id)
    if team is None or team.deleted_at is not None:
        return False
    return team.open_to_all or await seat_of(session, team_id, user_id) is not None


def may_remove(actor: Seat | None, target: Seat, *, own: bool) -> bool:
    """Whether someone in seat ``actor`` takes a ``target`` off the team.
    ``own``: the target is the actor, leaving. The owner never leaves or is
    removed; the team changes hands by transfer."""
    if target is Seat.owner:
        return False
    if own:
        return True
    return (
        actor is not None
        and Permission.manage_members in GRANTS[actor]
        and RANK[actor] > RANK[target]
    )
