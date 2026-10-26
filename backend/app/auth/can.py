"""Whether a person may do one thing to one object: the one question every
route asks, whatever the object.

A permission names its kind of object by its type — a team's
(``app.domain.team.permissions.Permission``) is asked of a team, a space's of a
space, a problem's of a problem, and so on — and each kind answers it from its own seats. A route declares what it
needs (``app.api.write_access.needs``) and :func:`require` decides it before
the handler runs.

What a person cannot see does not exist for them: :func:`require` answers 404
when the object is hidden from them, and 403 only when they see it but their
seat does not reach.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import ModuleType

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import task_roles as task
from app.core.errors import AccessDeniedError, NotFoundError
from app.domain.answers import permissions as answer
from app.domain.questions import permissions as question
from app.domain.space import permissions as space
from app.domain.team import permissions as team

#: Every permission a route may declare, by the kind of object it is asked of.
Permission = (
    team.Permission
    | space.Permission
    | task.Permission
    | question.Permission
    | answer.Permission
)

#: Each kind's seats, by its permission type: the module answers ``may`` and
#: ``sees`` for an object of that kind.
_KINDS: dict[type, tuple[str, ModuleType]] = {
    team.Permission: ("team", team),
    space.Permission: ("space", space),
    task.Permission: ("task", task),
    question.Permission: ("question", question),
    answer.Permission: ("answer", answer),
}


def kind_of(permission: Permission) -> str:
    return _KINDS[type(permission)][0]


def _seats(permission: Permission) -> ModuleType:
    return _KINDS[type(permission)][1]


async def can(
    session: AsyncSession, user_id: int, permission: Permission, on: int
) -> bool:
    may: Callable[..., Awaitable[bool]] = _seats(permission).may
    return await may(session, on, user_id, permission)


async def sees(
    session: AsyncSession, user_id: int, permission: Permission, on: int
) -> bool:
    """Whether the object the permission is asked of exists for this person."""
    seen: Callable[..., Awaitable[bool]] = _seats(permission).sees
    return await seen(session, on, user_id)


async def require(
    session: AsyncSession, user_id: int, permission: Permission, on: int
) -> None:
    if await can(session, user_id, permission, on):
        return
    kind = kind_of(permission)
    if not await sees(session, user_id, permission, on):
        raise NotFoundError.for_resource(kind, on)
    raise AccessDeniedError(action=permission.value, resource_type=kind, resource_id=on)
