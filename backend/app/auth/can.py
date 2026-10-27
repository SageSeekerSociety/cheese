"""Whether a person may do one thing to one object: the one question every
route asks, whatever the object.

A permission's type names the kind of object it is asked of — a team's
(``app.domain.team.permissions.Permission``) of a team, a space's of a space —
and :data:`POLICIES` gives each kind's :class:`~app.core.policy.Policy`. A
route names what it needs with ``Can(...)`` (``app.api.write_access``), and
:func:`authorize` decides it before the handler runs.

What a person cannot see does not exist for them: :func:`authorize` answers
404 when the object is missing or hidden from them, and 403 only when they see
it but may not do this to it.
"""

from __future__ import annotations

import enum
from typing import Any, NoReturn

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.task_roles import CLAIM, TASK
from app.core.errors import AccessDeniedError, NotFoundError
from app.core.policy import Policy
from app.domain.answers.permissions import ANSWER
from app.domain.questions.permissions import QUESTION
from app.domain.space.permissions import SPACE
from app.domain.team.permissions import TEAM

#: Each kind's policy, by the type of its permissions.
POLICIES: dict[type[enum.StrEnum], Policy[Any, Any]] = {
    policy.permissions: policy
    for policy in (TEAM, SPACE, TASK, CLAIM, QUESTION, ANSWER)
}


def policy_of(permission: enum.StrEnum) -> Policy[Any, Any]:
    return POLICIES[type(permission)]


def refuse(permission: enum.StrEnum, on: int) -> NoReturn:
    """The person sees the object but may not do this to it."""
    raise AccessDeniedError(
        action=permission.value,
        resource_type=policy_of(permission).kind,
        resource_id=on,
    )


def refuse_unless(allowed: bool, permission: enum.StrEnum, on: int) -> None:
    """For a rule that turns on more than the person's standing — removing
    someone below one's own seat, reading one's own claim — asked by the handler
    once :func:`authorize` has let the person in."""
    if not allowed:
        refuse(permission, on)


async def authorize(
    session: AsyncSession, user_id: int, permission: enum.StrEnum, on: int
) -> Any:
    """The object, if the person may do ``permission`` to it; else 404 or 403."""
    policy = policy_of(permission)
    obj = await policy.load(session, on)
    if obj is None:
        raise NotFoundError.for_resource(policy.kind, on)
    if await policy.may(session, obj, user_id, permission):
        return obj
    if not await policy.sees(session, obj, user_id):
        raise NotFoundError.for_resource(policy.kind, on)
    refuse(permission, on)


async def require(
    session: AsyncSession, user_id: int, permission: enum.StrEnum, obj: Any
) -> None:
    """:func:`authorize` for an object the handler already holds — a claim it
    read through the problem in the path, say: 403 unless the person may."""
    if not await policy_of(permission).may(session, obj, user_id, permission):
        refuse(permission, obj.id)
