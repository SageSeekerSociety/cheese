"""What a person may do to an answer.

Everyone signed in reads an answer, votes on it and comments on it; editing or
deleting it is its author's. A route declares it (``app.api.write_access.Can``)
and ``app.auth.can`` asks the policy; an answer that was deleted, or never was,
does not exist for anyone.
"""

import enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.policy import Policy
from app.domain.answers.models import Answer


class Permission(enum.StrEnum):
    #: Edit or delete the answer.
    manage = "manage"


class AnswerPolicy(Policy[Answer, Permission]):
    kind = "answer"
    path_param = "answer_id"
    permissions = Permission

    async def load(self, session: AsyncSession, id: int) -> Answer | None:
        found = await session.get(Answer, id)
        return None if found is None or found.deleted_at is not None else found

    async def may(
        self, session: AsyncSession, obj: Answer, user_id: int, permission: Permission
    ) -> bool:
        return obj.created_by_id == user_id


ANSWER = AnswerPolicy()
