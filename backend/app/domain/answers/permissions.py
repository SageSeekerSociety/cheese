"""What a person may do to an answer.

Everyone signed in reads an answer, votes on it and comments on it; editing or
deleting it is its author's. A route declares it (``app.api.write_access.needs``)
and ``app.auth.can`` asks :func:`may`; an answer that was deleted, or never was,
does not exist for anyone (:func:`sees`).
"""

import enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.answers.models import Answer


class Permission(enum.StrEnum):
    #: Edit or delete the answer.
    manage = "manage"


async def _answer(session: AsyncSession, answer_id: int) -> Answer | None:
    found = await session.get(Answer, answer_id)
    return None if found is None or found.deleted_at is not None else found


async def may(
    session: AsyncSession, answer_id: int, user_id: int, permission: Permission
) -> bool:
    found = await _answer(session, answer_id)
    return found is not None and found.created_by_id == user_id


async def sees(session: AsyncSession, answer_id: int, user_id: int) -> bool:
    return await _answer(session, answer_id) is not None
