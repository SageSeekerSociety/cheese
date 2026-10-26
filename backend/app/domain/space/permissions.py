"""Who someone is in a space (题目版), and what that lets them do.

A space has one owner, admins (a course's teachers) and members. The owner and
admins are rows in ``space_admin_relation``; a member is a ``space_member`` row.
Each seat holds a fixed set of the permissions below; the owner holds all of
them. :func:`seat_of` says where a person sits and :func:`may` whether that seat
holds a permission; a route declares what it needs and ``app.auth.can`` asks
these. The space's services keep only the space's own rules.

Anyone with no seat does not see the space at all (:func:`sees`): what you
cannot see does not exist for you.
"""

import enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import (
    Space,
    SpaceAdminRelation,
    SpaceAdminRole,
    SpaceMember,
)


class Seat(enum.StrEnum):
    owner = "owner"
    admin = "admin"
    member = "member"


class Permission(enum.StrEnum):
    #: See the space and what is in it: its people, categories, announcements,
    #: materials, and one's own problems there.
    view = "view"
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
    Seat.member: frozenset({Permission.view}),
}


async def seat_of(session: AsyncSession, space_id: int, user_id: int) -> Seat | None:
    """Where the person sits in the space; ``None`` when they have no seat."""
    role = await session.scalar(
        select(SpaceAdminRelation.role).where(
            SpaceAdminRelation.space_id == space_id,
            SpaceAdminRelation.user_id == user_id,
            SpaceAdminRelation.deleted_at.is_(None),
        )
    )
    if role is not None:
        return Seat.owner if role == SpaceAdminRole.OWNER.value else Seat.admin
    member = await session.scalar(
        select(SpaceMember.id).where(
            SpaceMember.space_id == space_id,
            SpaceMember.user_id == user_id,
            SpaceMember.deleted_at.is_(None),
        )
    )
    return Seat.member if member is not None else None


async def may(
    session: AsyncSession, space_id: int, user_id: int, permission: Permission
) -> bool:
    space = await session.get(Space, space_id)
    if space is None or space.deleted_at is not None:
        return False
    seat = await seat_of(session, space_id, user_id)
    return seat is not None and permission in GRANTS[seat]


async def sees(session: AsyncSession, space_id: int, user_id: int) -> bool:
    """Whether the space exists for this person: they hold a seat in it."""
    return await may(session, space_id, user_id, Permission.view)
