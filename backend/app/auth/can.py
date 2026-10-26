"""Whether a person may do one thing to one object: the one question every
route asks, whatever the object.

A permission names its kind of object by its type — a team's
(``app.domain.team.permissions.Permission``) is asked of a team — and each kind
answers it from its own seats (``app.domain.team.permissions``). A route declares what it needs
(``app.api.write_access.Needs``) and :func:`require` decides it before the
handler runs.

What a person cannot see does not exist for them: :func:`require` answers 404
when the object is hidden from them, and 403 only when they see it but their
seat does not reach.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessDeniedError, NotFoundError
from app.domain.team import permissions as team

#: Every permission a route may declare, by the kind of object it is asked of.
Permission = team.Permission

_KIND: dict[type, str] = {team.Permission: "team"}


def kind_of(permission: Permission) -> str:
    return _KIND[type(permission)]


async def can(
    session: AsyncSession, user_id: int, permission: Permission, on: int
) -> bool:
    return await team.may(session, on, user_id, permission)


async def sees(
    session: AsyncSession, user_id: int, permission: Permission, on: int
) -> bool:
    """Whether the object the permission is asked of exists for this person."""
    return await team.sees(session, on, user_id)


async def require(
    session: AsyncSession, user_id: int, permission: Permission, on: int
) -> None:
    if await can(session, user_id, permission, on):
        return
    kind = kind_of(permission)
    if not await sees(session, user_id, permission, on):
        raise NotFoundError.for_resource(kind, on)
    raise AccessDeniedError(action=permission.value, resource_type=kind, resource_id=on)
