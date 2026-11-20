"""What a person may do to one of their own conversations, rules or memories.

Everything in ``app.domain.assistant`` belongs to exactly one person and to
nobody else: a conversation, a rule 芝士 keeps for them, what it remembers.
There is no seat to hold and no team to be in, so the question is only «is this
row theirs?». A stranger holding the id is answered as if the row did not
exist — 404, as ``routes/assistant.py`` already answers for a conversation
that is someone else's, so an id confirms nothing.

Routes declare it with ``Can(Permission.manage)`` (``app.api.write_access``)
and ``app.auth.can`` asks :data:`ROUTINE`. A rule's id is a UUID, so the policy
says so with ``id_type``; the parameter the route declares is validated as one.
"""

import enum
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.policy import OwnerPolicy
from app.domain.assistant.models import AssistantRoutine


class Permission(enum.StrEnum):
    #: Change or delete a rule of one's own.
    manage = "manage"


class RoutinePolicy(OwnerPolicy[AssistantRoutine, Permission]):
    kind = "routine"
    path_param = "routineId"
    permissions = Permission
    id_type = uuid.UUID

    async def load(
        self, session: AsyncSession, id: int | uuid.UUID
    ) -> AssistantRoutine | None:
        return await session.get(AssistantRoutine, id)

    async def owner(self, obj: AssistantRoutine) -> int | None:
        return obj.user_id


ROUTINE = RoutinePolicy()
