"""The actor seam: turn a request's credentials into WHO is acting.

Humans and agents resolve to the *same* ``Actor``, and it carries no field that
says which kind it is. The shape used to say that and then hand out the hook
anyway — an ``is_agent`` flag resolved here and read eight routes away, where
whoever read it was deciding authorization, a recipient or a signature from the
KIND of the participant rather than from what it holds. A participant is a
handle; what it may do in a room is its seat (``topic_memberships``), and
whether a handle carries an agent-binding is derived where that is genuinely
the question (``IdentityService.is_agent``) rather than travelled along.

Resolution order (fusion-design §4: "actor 在信任边界注入,永不从 body 读"):

1. **Human session token** (``Authorization: Bearer`` / WS ``?token=``) — the
   verified handle, `via="token"`.
2. **Agent scoped token** (``X-Cheese-Token`` on a cheese-gated route) — resolves
   to the ``cheese`` agent-user, `via="cheese"`.
3. **Delegated credential** (``X-Cheese-Token``, a 芝士 answering someone) —
   resolves to the person it answers, `via="delegated"`, on the few routes that
   accept it (``app.api.auth.DELEGATED_ROUTES``).

Nothing else names the caller. A handle in a body or a query parameter is never
an identity, so a request with neither credential resolves to nobody.

Pure + adapter-injected (照 reference attribution.py / viewer_authz.py): the
resolution rule is unit-tested without a request, DB or WebSocket. The concrete
wiring lives in ``app.api.auth``.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# Adapters injected at the trust boundary.
TokenVerifier = Callable[[str], "TokenIdentity | None"]  # bearer/query token → identity
CheeseVerifier = Callable[[], Awaitable[bool]]  # X-Cheese-Token valid for THIS route?


@dataclass(frozen=True)
class TokenIdentity:
    """What a verified human token asserts."""

    handle: str
    # fusion unify P3: main's User PK is an int (table ``user``). A verified
    # main-minted token carries it in the ``sub`` claim; legacy cheesex tokens
    # (handle in ``sub``) have no int id → None.
    user_id: int | None


@dataclass(frozen=True)
class Actor:
    """Who is acting, resolved at the trust boundary. ``handle`` stays the
    authorship key; ``user_id`` is main's int User PK when a real token carries
    it (needed to bind int-keyed rows like ``device.owner_user_id``)."""

    handle: str
    user_id: int | None
    via: str  # "token" | "cheese" | "delegated" | "anonymous"

    @property
    def authenticated(self) -> bool:
        """True when the actor came from a verified credential (token or the
        agent's scoped token), False for the anonymous placeholder."""
        return self.via in ("token", "cheese", "delegated")


async def resolve_actor(
    *,
    bearer_token: str | None,
    verify_token: TokenVerifier,
    cheese_valid: CheeseVerifier,
    cheese_handle: str,
) -> Actor | None:
    """Resolve a request to its actor. Returns ``None`` when no credential
    identifies the caller (no valid token, no valid cheese token) — the caller
    decides whether that is anonymous-ok or a 401."""
    # 1. Human session token wins — the handle is the token's, never the body's.
    if bearer_token:
        identity = verify_token(bearer_token)
        if identity is not None:
            return Actor(
                handle=identity.handle,
                user_id=identity.user_id,
                via="token",
            )
    # 2. Agent scoped token → the cheese agent-user.
    if await cheese_valid():
        return Actor(
            handle=cheese_handle,
            user_id=None,
            via="cheese",
        )
    return None
