"""Who someone is in a space (题目版), and what that lets them do.

A space has one owner, admins (a course's teachers) and members. The owner and
admins are rows in ``space_admin_relation``; a member is a ``space_member`` row.
Each seat holds a fixed set of the permissions below; the owner holds all of
them. ``seat_of`` says where a person sits and :data:`SPACE` whether that seat
holds a permission; a route declares what it needs and ``app.auth.can`` asks
these. The space's services keep only the space's own rules.

Anyone with no seat does not see the space at all: what you
cannot see does not exist for you.
"""

import enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.policy import SeatPolicy
from app.core.request_facts import recall, remember
from app.domain.space.models import (
    Space,
    SpaceAdminRelation,
    SpaceAdminRole,
    SpaceMember,
)
from app.domain.space.repositories import (
    SpaceAdminRelationRepository,
    SpaceRepository,
)


class Seat(enum.StrEnum):
    owner = "owner"
    admin = "admin"
    member = "member"


class Permission(enum.StrEnum):
    #: See the space and what is in it: its people, categories, announcements,
    #: materials, and one's own problems there.
    view = "view"
    #: Publish a problem to the space. Anyone in it may; a problem goes up only
    #: once someone who teaches the space approves it.
    publish = "publish"
    #: Teach every problem in the space: approve or send back what was
    #: published, grade, and manage who takes part — as each problem's own
    #: setter does for theirs (``app.auth.task_roles``).
    teach = "teach"
    #: The space's name, intro and limits, its categories, topics and email
    #: domain groups.
    edit_settings = "edit_settings"
    #: Add and remove members, and manage the invite codes that let people in.
    manage_members = "manage_members"
    #: Publish, change and take down announcements to everyone in the space.
    announce = "announce"
    #: Upload, change and remove the space's materials.
    manage_materials = "manage_materials"
    #: The space's analytics, submissions and participant exports.
    view_analytics = "view_analytics"
    #: Make someone an admin, step one down, or hand the space over.
    manage_admins = "manage_admins"
    #: Delete the space, or send it back for review after a rejection.
    delete = "delete"
    resubmit = "resubmit"


_ADMIN = frozenset(Permission) - {
    Permission.manage_admins,
    Permission.delete,
    Permission.resubmit,
}

GRANTS: dict[Seat, frozenset[Permission]] = {
    Seat.owner: frozenset(Permission),
    Seat.admin: _ADMIN,
    Seat.member: frozenset({Permission.view, Permission.publish}),
}


async def seat_of(session: AsyncSession, space_id: int, user_id: int) -> Seat | None:
    """Where the person sits in the space; ``None`` when they have no seat.

    A problem's page asks it from each check that needs it (seeing the
    problem, teaching it, the payload's own flags), so a request answers it
    once; the next request asks again, and an admin stepped down is refused
    there."""
    key = ("space_seat", space_id, user_id)
    found, cached = recall(session, key)
    if found:
        return cached
    seat: Seat | None = None
    relation = await SpaceAdminRelationRepository(session).get_relation(
        space_id, user_id
    )
    if relation is not None:
        seat = Seat.owner if relation.role == SpaceAdminRole.OWNER.value else Seat.admin
    elif await session.scalar(
        select(SpaceMember.id).where(
            SpaceMember.space_id == space_id,
            SpaceMember.user_id == user_id,
            SpaceMember.deleted_at.is_(None),
        )
    ):
        seat = Seat.member
    remember(
        session,
        key,
        seat,
        tables=frozenset({"space_admin_relation", "space_member"}),
    )
    return seat


class SpacePolicy(SeatPolicy[Space, Permission, Seat]):
    """Anyone with no seat does not see the space at all."""

    kind = "space"
    path_param = "spaceId"
    permissions = Permission
    grants = GRANTS

    async def load(self, session: AsyncSession, id: int) -> Space | None:
        return await SpaceRepository(session).get_by_id(id)

    async def seat(
        self, session: AsyncSession, obj: Space, user_id: int
    ) -> Seat | None:
        return await seat_of(session, obj.id, user_id)


SPACE = SpacePolicy()


async def runs(
    session: AsyncSession, space_id: int | None, user_id: int | None
) -> bool:
    """Whether the person is the space's owner or one of its admins. Nobody
    asking (no id) and no space are no; asked afresh every time, so an admin
    stepped down is refused on their next request."""
    if space_id is None or user_id is None:
        return False
    return await seat_of(session, space_id, user_id) in (Seat.owner, Seat.admin)


async def managers(session: AsyncSession, space_id: int | None) -> set[int]:
    """The user ids of the space's owner and admins, in one query."""
    if space_id is None:
        return set()
    return set(
        await session.scalars(
            select(SpaceAdminRelation.user_id).where(
                SpaceAdminRelation.space_id == space_id,
                SpaceAdminRelation.deleted_at.is_(None),
            )
        )
    )
