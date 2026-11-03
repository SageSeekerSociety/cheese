"""What a person may do to a question.

Everyone signed in reads a question, answers it and follows it. What changes
the question itself is its asker's: editing or deleting it, setting a bounty,
accepting an answer or taking the acceptance back, and withdrawing an
invitation to answer. A route declares it (``app.api.write_access.Can``) and
``app.auth.can`` asks the policy; a question that was deleted, or never was,
does not exist for anyone.
"""

import enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.policy import Policy
from app.domain.questions.models import Question


class Permission(enum.StrEnum):
    #: Edit or delete the question, set its bounty, accept an answer or take
    #: the acceptance back, withdraw an invitation to answer it.
    manage = "manage"


class QuestionPolicy(Policy[Question, Permission]):
    kind = "question"
    path_param = "question_id"
    permissions = Permission

    async def load(self, session: AsyncSession, id: int) -> Question | None:
        found = await session.get(Question, id)
        return None if found is None or found.deleted_at is not None else found

    async def may(
        self, session: AsyncSession, obj: Question, user_id: int, permission: Permission
    ) -> bool:
        return obj.created_by_id == user_id


QUESTION = QuestionPolicy()
