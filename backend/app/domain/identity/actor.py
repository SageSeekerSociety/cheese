"""The actor seam: turn a request's credentials into WHO is acting.

Humans and agents resolve to the *same* ``Actor`` shape so domain services never
branch on ``if is_agent`` (fusion-design §2). Resolution order (fusion-design §4:
"actor 在信任边界注入,永不从 body 读"):

1. **Human session token** (``Authorization: Bearer`` / WS ``?token=``) — the
   verified handle, `via="token"`.
2. **User-issued agent token** (same header/param, a ``cxat_`` secret instead of
   a JWT) — a member's OWN agent acting for them: `via="agent"`, is_agent=True,
   its own handle, and ``owner_handle`` naming the human it answers for.
3. **Agent scoped token** (``X-Cheese-Token`` on a cheese-gated route) — resolves
   to the ``cheese`` agent-user, `via="cheese"`.
4. **Phase-0 handle fallback** — the handle a caller passed in the body/param,
   `via="handle"`. Deprecated (logged); kept so no existing call breaks while the
   frontend migrates to tokens.

Pure + adapter-injected (照 reference attribution.py / viewer_authz.py): the
resolution rule is unit-tested without a request, DB or WebSocket. The concrete
wiring lives in ``app.api.auth``.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# Adapters injected at the trust boundary.
TokenVerifier = Callable[[str], "TokenIdentity | None"]  # bearer/query token → identity
CheeseVerifier = Callable[[], Awaitable[bool]]  # X-Cheese-Token valid for THIS route?
AgentDeriver = Callable[[str], Awaitable[bool]]  # handle → carries an agent-binding?
# bearer/query secret → the delegated agent it authenticates, if any
DelegatedVerifier = Callable[[str], Awaitable["DelegatedIdentity | None"]]


@dataclass(frozen=True)
class TokenIdentity:
    """What a verified human token asserts."""

    handle: str
    # fusion unify P3: main's User PK is an int (table ``user``). A verified
    # main-minted token carries it in the ``sub`` claim; legacy cheesex tokens
    # (handle in ``sub``) have no int id → None.
    user_id: int | None


@dataclass(frozen=True)
class DelegatedIdentity:
    """What a live user-issued agent token resolves to: the agent user it acts
    as, plus the human whose permissions bound it."""

    agent_handle: str
    agent_user_id: int
    owner_handle: str


@dataclass(frozen=True)
class Actor:
    """Who is acting, resolved at the trust boundary. ``handle`` stays the
    authorship key; ``user_id`` is main's int User PK when a real token carries
    it (needed to bind int-keyed rows like ``device.owner_user_id``)."""

    handle: str
    user_id: int | None
    is_agent: bool
    via: str  # "token" | "agent" | "cheese" | "handle"
    # Set only for a member's own agent (via="agent"): the human it acts for.
    # Authorization reads it so a delegated agent gets exactly its owner's
    # access — never more, never the blanket pass a platform agent gets.
    owner_handle: str | None = None

    @property
    def authenticated(self) -> bool:
        """True when the actor came from a verified credential (a human token, a
        user-issued agent token, or the platform agent's scoped token), False for
        the Phase-0 handle fallback. Authorization enforces membership/role only
        for authenticated actors — the fallback stays permissive so pre-token
        callers keep working."""
        return self.via in ("token", "agent", "cheese")


async def resolve_actor(
    *,
    bearer_token: str | None,
    verify_token: TokenVerifier,
    cheese_valid: CheeseVerifier,
    is_agent: AgentDeriver,
    cheese_handle: str,
    fallback_handle: str | None,
    verify_delegated: DelegatedVerifier | None = None,
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
    # 2. A member's own agent token, presented the same way. Tried after the JWT
    # so an existing session token never pays for a database lookup, and so this
    # can add no way to lose an identity the old path already resolved.
    if bearer_token and verify_delegated is not None:
        delegated = await verify_delegated(bearer_token)
        if delegated is not None:
            return Actor(
                handle=delegated.agent_handle,
                user_id=delegated.agent_user_id,
                is_agent=True,
                via="agent",
                owner_handle=delegated.owner_handle,
            )
    # 3. Agent scoped token → the cheese agent-user.
    if await cheese_valid():
        return Actor(
            handle=cheese_handle,
            user_id=None,
            is_agent=True,
            via="cheese",
        )
    # 4. Phase-0 fallback: trust the passed handle (deprecated).
    handle = (fallback_handle or "").strip()
    if not handle:
        return None
    return Actor(
        handle=handle,
        user_id=None,
        is_agent=await is_agent(handle),
        via="handle",
    )
