"""The actor seam: turn a request's credentials into WHO is acting.

Humans and agents resolve to the *same* ``Actor`` shape so domain services never
branch on ``if is_agent`` (fusion-design §2). Resolution order (fusion-design §4:
"actor 在信任边界注入,永不从 body 读"):

1. **Human session token** (``Authorization: Bearer`` / WS ``?token=``) — the
   verified handle, `via="token"`.
2. **Agent scoped token** (``X-Cheese-Token`` on a cheese-gated route) — resolves
   to the ``cheese`` agent-user, `via="cheese"`.
3. **Phase-0 handle fallback** — the handle a caller passed in the body/param,
   `via="handle"`. Deprecated (logged); kept so no existing call breaks while the
   frontend migrates to tokens.

Pure + adapter-injected (照 reference attribution.py / viewer_authz.py): the
resolution rule is unit-tested without a request, DB or WebSocket. The concrete
wiring lives in ``app.api.auth``.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# Adapters injected at the trust boundary.
TokenVerifier = Callable[[str], "TokenIdentity | None"]  # bearer/query token → identity
CheeseVerifier = Callable[[], Awaitable[bool]]  # X-Cheese-Token valid for THIS route?
AgentDeriver = Callable[[str], Awaitable[bool]]  # handle → carries an agent-binding?


@dataclass(frozen=True)
class TokenIdentity:
    """What a verified human token asserts."""

    handle: str
    user_id: uuid.UUID | None


@dataclass(frozen=True)
class Actor:
    """Who is acting, resolved at the trust boundary. ``handle`` stays the
    authorship key (no user_id data migration in P1); ``is_agent`` is derived."""

    handle: str
    user_id: uuid.UUID | None
    is_agent: bool
    via: str  # "token" | "cheese" | "handle"

    @property
    def authenticated(self) -> bool:
        """True when the actor came from a verified credential (token or the
        agent's scoped token), False for the Phase-0 handle fallback. Authorization
        enforces membership/role only for authenticated actors — the fallback stays
        permissive so pre-token callers keep working."""
        return self.via in ("token", "cheese")


async def resolve_actor(
    *,
    bearer_token: str | None,
    verify_token: TokenVerifier,
    cheese_valid: CheeseVerifier,
    is_agent: AgentDeriver,
    cheese_handle: str,
    fallback_handle: str | None,
) -> Actor | None:
    """Resolve a request to its actor. Returns ``None`` only when nothing
    identifies the caller (no token, no valid cheese token, no fallback handle) —
    the caller decides whether that is anonymous-ok or a 401."""
    # 1. Human session token wins — the handle is the token's, never the body's.
    if bearer_token:
        identity = verify_token(bearer_token)
        if identity is not None:
            return Actor(
                handle=identity.handle,
                user_id=identity.user_id,
                is_agent=await is_agent(identity.handle),
                via="token",
            )
    # 2. Agent scoped token → the cheese agent-user.
    if await cheese_valid():
        return Actor(
            handle=cheese_handle,
            user_id=None,
            is_agent=True,
            via="cheese",
        )
    # 3. Phase-0 fallback: trust the passed handle (deprecated).
    handle = (fallback_handle or "").strip()
    if not handle:
        return None
    return Actor(
        handle=handle,
        user_id=None,
        is_agent=await is_agent(handle),
        via="handle",
    )
