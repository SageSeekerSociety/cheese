"""Identity business logic: agent-as-user seeding + the is-agent derivation.

芝士 is a first-class ``User`` row (handle ``cheese``) with a ``platform``
agent-binding. ``ensure_agent_user`` is idempotent so startup / migration can
guarantee it exists without ever duplicating it. ``is_agent`` derives the flag
from the binding — never from a column or a hard-coded handle check.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.models import AgentBindingKind
from app.domain.identity.repositories import AgentBindingRepository
from app.domain.user.models import User
from app.domain.user.repositories import UserRepository

# 芝士's stable handle — a real user row, seeded once. Kept here as the single
# source of truth (topic_membership re-exports it for the roster seed).
CHEESE_HANDLE = "cheese"
CHEESE_NAME = "芝士"


class IdentityService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._users = UserRepository(session)
        self._bindings = AgentBindingRepository(session)

    async def ensure_agent_user(
        self, *, handle: str = CHEESE_HANDLE, name: str = CHEESE_NAME
    ) -> User:
        """Idempotently ensure an agent user row + its platform binding exist.
        Safe to call at every startup and from the migration — never duplicates."""
        user = await self._users.get_by_handle(handle)
        if user is None:
            user = await self._users.add(User(handle=handle, name=name))
        if await self._bindings.get_for_user(user.id) is None:
            await self._bindings.add(
                user_id=user.id, kind=AgentBindingKind.platform
            )
        return user

    async def is_agent(self, handle: str) -> bool:
        """True iff the handle names a user carrying an agent-binding."""
        user = await self._users.get_by_handle(handle)
        if user is None:
            return False
        return await self._bindings.get_for_user(user.id) is not None
