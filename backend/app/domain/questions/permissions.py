"""What a person may do to a question.

Everyone signed in reads a question, answers it and follows it. What changes
the question itself is its asker's: editing or deleting it, setting a bounty,
accepting an answer or taking the acceptance back, and withdrawing an
invitation to answer. A route declares it (``app.api.write_access.needs``) and
``app.auth.can`` asks :func:`may`; a question that was deleted, or never was,
does not exist for anyone (:func:`sees`).
"""

import enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.questions.models import Question


class Permission(enum.StrEnum):
    #: Edit or delete the question, set its bounty, accept an answer or take
    #: the acceptance back, withdraw an invitation to answer it.
    manage = "manage"


async def _question(session: AsyncSession, question_id: int) -> Question | None:
    found = await session.get(Question, question_id)
    return None if found is None or found.deleted_at is not None else found


async def may(
    session: AsyncSession, question_id: int, user_id: int, permission: Permission
) -> bool:
    found = await _question(session, question_id)
    return found is not None and found.created_by_id == user_id


async def sees(session: AsyncSession, question_id: int, user_id: int) -> bool:
    return await _question(session, question_id) is not None
