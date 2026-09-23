"""Identity business logic: agent-as-user seeding + the is-agent derivation.

芝士 is a first-class ``User`` row with a ``platform`` agent-binding.
``ensure_agent_user`` is idempotent so startup / migration can guarantee it
exists without ever duplicating it. ``is_agent`` derives the flag from the
binding — never from a column or a hard-coded handle check.

**One agent-user per topic** (分身独立身份). The platform-wide ``cheese`` row is
the fallback identity only; every topic's 分身 gets its OWN row, handle
``cheese-<topic hex>``, so an action is attributable to the 分身 that took it and
a single 分身 can be de-authorized (drop it from the topic roster) without waiting
for its token to expire. This mirrors the device-screen agent (一个 agent 是一个
屏幕, ``app.domain.agent.device_attribution``): identity per execution context,
not one collapsed platform account.

Display collapses even though identity forks: every agent user carries a profile
whose nickname is 芝士, so the UI keeps showing one familiar name while the audit
trail keeps the distinct handles.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.handles import (
    AGENT_HANDLE_PREFIX,
    CHEESE_HANDLE,
    CHEESE_NAME,
    agent_instance_handle,
    looks_like_agent_handle,
)
from app.domain.identity.models import AgentBindingKind
from app.domain.identity.repositories import AgentBindingRepository
from app.domain.user.models import User
from app.domain.user.repositories import UserProfileRepository, UserRepository

# Handle naming lives in the dependency-free ``handles`` module (the token minter
# imports it); re-exported here so every existing import keeps working.
__all__ = [
    "AGENT_HANDLE_PREFIX",
    "CHEESE_HANDLE",
    "CHEESE_NAME",
    "IdentityService",
    "agent_instance_handle",
    "looks_like_agent_handle",
]


class IdentityService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._users = UserRepository(session)
        self._bindings = AgentBindingRepository(session)
        self._profiles = UserProfileRepository(session)

    async def _create_agent_user(self, *, handle: str) -> User:
        """Create an agent as a real (main-repo) User row: username == handle,
        a placeholder agent email, no password (agents don't log in). Fusion A2:
        the merged app has ONE identity table (``user``) — agents live there too."""
        return await self._users.create_user(
            username=handle,
            email=f"{handle}@agent.cheese.local",
            hashed_password=None,
        )

    async def ensure_agent_user(
        self, *, handle: str = CHEESE_HANDLE, name: str = CHEESE_NAME
    ) -> User:
        """Idempotently ensure an agent user row + its platform binding + its
        display profile exist. Safe to call at every startup, from the migration
        and on every turn — never duplicates."""
        user = await self._users.get_by_handle(handle)
        if user is None:
            try:
                user = await self._create_agent_user(handle=handle)
            except IntegrityError:
                # Two first turns raced; the other one's row is the agent.
                user = await self._users.get_by_handle(handle)
                if user is None:
                    raise
        if await self._bindings.get_for_user(user.id) is None:
            await self._bindings.add(user_id=user.id, kind=AgentBindingKind.platform)
        # The display name lives on the profile (the User row only carries the
        # handle). Without it every agent would render as its raw
        # ``cheese-<hex>`` handle instead of 芝士 — identity forks, display does not.
        if await self._profiles.get_profile_by_user_id(user.id) is None:
            try:
                await self._profiles.create_profile(
                    user_id=user.id, nickname=name, intro="", avatar_id=1
                )
            except IntegrityError:
                if await self._profiles.get_profile_by_user_id(user.id) is None:
                    raise
        return user

    async def ensure_room_agent_user(self, topic_id: uuid.UUID) -> User:
        """The agent-user row of whoever answers this room (identity, not display).

        Every caller of this wants the same thing — a numeric user id to hang a
        session, a lease or a machine on — and the answer is the agent, never the
        room: a room may seat several, and the same agent keeps one identity
        across all of them. The roster says which one (its seat is the grant),
        and a room with no agent seated falls back to the project's own 芝士.
        """
        from app.domain.topic_membership.services import TopicMemberService

        handle = await TopicMemberService(self._session).resolve_agent_handle(topic_id)
        return await self.ensure_agent_user(handle=handle, name=CHEESE_NAME)

    async def ensure_instance_agent_user(
        self, instance_id: uuid.UUID, display_name: str
    ) -> User:
        """Ensure the agent-user row for a project's agent (identity, display).

        The handle is derived from the agent, not from a room: an agent that
        joins three rooms is one collaborator with one identity, and two agents
        in one room are two. Idempotent, so an agent created before this existed
        gets its identity the next time anything asks for it.
        """
        return await self.ensure_agent_user(
            handle=agent_instance_handle(instance_id),
            name=display_name.strip() or CHEESE_NAME,
        )

    async def is_agent(self, handle: str) -> bool:
        """True iff the handle names a user carrying an agent-binding."""
        user = await self._users.get_by_handle(handle)
        if user is None:
            return False
        return await self._bindings.get_for_user(user.id) is not None

    async def agents_among(self, handles: Sequence[str]) -> set[str]:
        """:meth:`is_agent` 的批量版：这些 handle 里哪些是 agent-user，两次往返。

        名册要对整张表问这一句。逐个 ``is_agent`` 是 N+1——一个 30 人的项目就是 60
        次往返，而判据本身（带不带 agent-binding）一次就能全部问出来。
        """
        if not handles:
            return set()
        users = await self._users.get_by_handles(list(handles))
        agent_ids = await self._bindings.agent_user_ids(
            [user.id for user in users.values()]
        )
        return {handle for handle, user in users.items() if user.id in agent_ids}
