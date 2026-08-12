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
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_tokens import display_prefix, generate_token, hash_token
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.domain.identity.actor import DelegatedIdentity
from app.domain.identity.handles import (
    CHEESE_HANDLE,
    CHEESE_NAME,
    TOPIC_AGENT_PREFIX,
    delegated_agent_handle,
    looks_like_agent_handle,
    topic_agent_handle,
)
from app.domain.identity.models import AgentBindingKind, AgentToken
from app.domain.identity.repositories import (
    AgentBindingRepository,
    AgentTokenRepository,
)
from app.domain.user.models import User
from app.domain.user.repositories import UserProfileRepository, UserRepository

# Handle naming lives in the dependency-free ``handles`` module (the token minter
# imports it); re-exported here so every existing import keeps working.
__all__ = [
    "CHEESE_HANDLE",
    "CHEESE_NAME",
    "DEFAULT_TOKEN_TTL_DAYS",
    "MAX_TOKEN_TTL_DAYS",
    "TOPIC_AGENT_PREFIX",
    "AgentTokenService",
    "IdentityService",
    "IssuedAgentToken",
    "looks_like_agent_handle",
    "topic_agent_handle",
]

# A credential that lives on a member's laptop should need renewing often enough
# that a forgotten one dies on its own, but not so often that the agent spends
# its life re-authenticating.
DEFAULT_TOKEN_TTL_DAYS = 30
MAX_TOKEN_TTL_DAYS = 365

# How far the "-agent", "-agent2", … search walks before giving up. A user with
# this many squatted variants has a naming problem, not a token problem.
_HANDLE_ATTEMPTS = 20

# Granularity of ``last_used_at`` — see AgentTokenService.authenticate.
_LAST_USED_RESOLUTION = timedelta(minutes=5)


class IdentityService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._users = UserRepository(session)
        self._bindings = AgentBindingRepository(session)
        self._profiles = UserProfileRepository(session)

    async def ensure_delegated_agent_user(self, owner: User) -> User:
        """The agent-user ``owner``'s own agent acts as — created on first use.

        Idempotent through the binding, not through the handle: the binding
        records whose agent this is, so re-issuing a token years later finds the
        same identity even if the naming rule has since changed. Only when there
        is no binding do we go looking for a free handle, and a name already
        taken by somebody else is skipped rather than hijacked.
        """
        existing = await self._bindings.get_for_owner(owner.id)
        if existing is not None:
            user = await self._users.get_by_id(existing.user_id)
            if user is not None:
                return user
        handle = await self._free_delegated_handle(owner.username)
        user = await self._create_agent_user(handle=handle)
        await self._bindings.add(
            user_id=user.id,
            kind=AgentBindingKind.delegated,
            owner_user_id=owner.id,
        )
        # Displayed instead of the raw handle wherever a nickname is shown, so a
        # reader sees whose agent spoke without decoding the handle.
        if await self._profiles.get_profile_by_user_id(user.id) is None:
            await self._profiles.create_profile(
                user_id=user.id,
                nickname=f"{owner.username} 的 agent",
                intro="",
                avatar_id=1,
            )
        return user

    async def _free_delegated_handle(self, owner_handle: str) -> str:
        for attempt in range(_HANDLE_ATTEMPTS):
            handle = delegated_agent_handle(owner_handle, attempt)
            if await self._users.get_by_handle(handle) is None:
                return handle
        raise ConflictError("没有可用的 agent handle，请先改用户名")

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
            user = await self._create_agent_user(handle=handle)
        if await self._bindings.get_for_user(user.id) is None:
            await self._bindings.add(user_id=user.id, kind=AgentBindingKind.platform)
        # The display name lives on the profile (the User row only carries the
        # handle). Without it every 分身 would render as its raw
        # ``cheese-<hex>`` handle instead of 芝士 — identity forks, display does not.
        if await self._profiles.get_profile_by_user_id(user.id) is None:
            await self._profiles.create_profile(
                user_id=user.id, nickname=name, intro="", avatar_id=1
            )
        return user

    async def ensure_topic_agent_user(self, topic_id: uuid.UUID) -> User:
        """Ensure the agent-user row for THIS topic's 分身 (identity, not display).

        Handle is derived from the topic id, so the sandbox token can name the
        acting 分身 without a DB lookup at mint time (``sandbox_auth``).
        """
        return await self.ensure_agent_user(
            handle=topic_agent_handle(topic_id), name=CHEESE_NAME
        )

    async def is_agent(self, handle: str) -> bool:
        """True iff the handle names a user carrying an agent-binding."""
        user = await self._users.get_by_handle(handle)
        if user is None:
            return False
        return await self._bindings.get_for_user(user.id) is not None


class IssuedAgentToken:
    """A freshly minted token plus its row. The plaintext ``secret`` exists only
    here, on the way out of the issuing request — nothing persists it, so this is
    the one and only time the user can read it."""

    def __init__(self, *, secret: str, row: AgentToken, agent_handle: str):
        self.secret = secret
        self.row = row
        self.agent_handle = agent_handle


class AgentTokenService:
    """Issue / list / revoke the credentials a member hands to their own agent,
    and authenticate the ones that come back.

    The permission story is the whole design: a token authenticates as a
    *separate* agent-user (so its writes are attributable), while authorization
    weighs the *owner* (so the agent can never reach anything its owner cannot).
    Neither half works alone — one identity would make the agent invisible, one
    permission set would make it a privilege-escalation primitive.
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._tokens = AgentTokenRepository(session)
        self._users = UserRepository(session)
        self._bindings = AgentBindingRepository(session)
        self._identity = IdentityService(session)

    async def issue(
        self, *, owner_user_id: int, name: str, ttl_days: int | None = None
    ) -> IssuedAgentToken:
        owner = await self._users.get_by_id(owner_user_id)
        if owner is None:
            raise NotFoundError("User not found")
        days = DEFAULT_TOKEN_TTL_DAYS if ttl_days is None else ttl_days
        if days < 1 or days > MAX_TOKEN_TTL_DAYS:
            raise BadRequestError(f"有效期需在 1 到 {MAX_TOKEN_TTL_DAYS} 天之间")
        agent = await self._identity.ensure_delegated_agent_user(owner)
        secret = generate_token()
        row = await self._tokens.add(
            owner_user_id=owner.id,
            agent_user_id=agent.id,
            name=name.strip()[:64],
            token_hash=hash_token(secret),
            token_prefix=display_prefix(secret),
            expires_at=datetime.now(UTC) + timedelta(days=days),
        )
        return IssuedAgentToken(secret=secret, row=row, agent_handle=agent.username)

    async def list_for_owner(self, owner_user_id: int) -> list[AgentToken]:
        return await self._tokens.list_for_owner(owner_user_id)

    async def revoke(self, *, token_id: uuid.UUID, owner_user_id: int) -> AgentToken:
        row = await self._tokens.get_for_owner(token_id, owner_user_id)
        if row is None:
            raise NotFoundError("Agent token not found")
        # Idempotent: revoking twice is not an error, and re-stamping the time
        # would misreport when access actually ended.
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
        return row

    async def authenticate(self, secret: str) -> DelegatedIdentity | None:
        """Resolve a presented secret to the agent it speaks for, or None.

        None covers every failure the same way — unknown, revoked, expired,
        dangling user — because a caller that learns *why* its credential was
        refused learns which of them exist.
        """
        row = await self._tokens.get_by_hash(hash_token(secret))
        if row is None or row.revoked_at is not None:
            return None
        if row.expires_at <= datetime.now(UTC):
            return None
        agent = await self._users.get_by_id(row.agent_user_id)
        owner = await self._users.get_by_id(row.owner_user_id)
        if agent is None or owner is None:
            return None
        # Coarse on purpose. This runs on every request the agent makes; stamping
        # it each time would put a write (and a row lock) on read-only traffic for
        # a field whose only job is to answer "still in use?".
        now = datetime.now(UTC)
        if row.last_used_at is None or now - row.last_used_at > _LAST_USED_RESOLUTION:
            row.last_used_at = now
        return DelegatedIdentity(
            agent_handle=agent.username,
            agent_user_id=agent.id,
            owner_handle=owner.username,
        )
