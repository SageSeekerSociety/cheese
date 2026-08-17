"""Human session tokens (P1 真人类鉴权, fusion-design §2/§4).

Login stays passwordless — the handle IS the identity (Phase 0 UX) — but a
successful login now also mints a signed **session token**. Every subsequent
request carries it as ``Authorization: Bearer <token>`` (or, for WebSockets that
cannot set headers, as a ``?token=`` query param), and the actor is resolved
from the *verified* token instead of a body field the caller could forge.

The token is a stock JWT (HS256) so claims (``exp``/``iat``/``type``) come for
free and it interoperates with the reference design's ``type == "access"``
convention. This is the human counterpart to the agent's HMAC scoped token in
``app.core.sandbox_auth`` — a valid token is **necessary, not sufficient**:
every write is still authorized against the actor's real membership/role
(``app.domain.authz``).
"""

from typing import TypedDict

import jwt

from app.core.config import settings

# fusion unify P3: human session tokens are minted by main's
# ``create_access_token`` (app.common.auth), which signs with
# ``settings.jwt_secret``. The verifier MUST use that same secret or every real
# login token is rejected here and the cheesex API layer sees an anonymous
# actor. ONE secret, no legacy chain: pre-merge cheesex tokens (signed with
# auth_token_secret / sandbox_token) simply fail verification and re-login.
_SECRET: str = settings.jwt_secret
_ALG = "HS256"
_TYPE = "access"


class TokenClaims(TypedDict):
    """The subset of JWT claims we rely on. ``sub`` is either the handle
    (cheesex-minted tokens) or the int user id as a string (main-minted tokens);
    ``handle`` is the explicit username claim main embeds for the fusion one-token
    story (falls back to sub). ``uid`` is a legacy uuid string if present."""

    sub: str
    handle: str | None
    uid: str | None
    type: str


def mint_session_token(
    *, handle: str, user_id: int | None, ttl_s: int | None = None
) -> str:
    """Sign a session token for a logged-in human. ``ttl_s`` overrides the
    configured lifetime (used only by tests exercising expiry)."""
    import time

    now = int(time.time())
    payload = {
        "sub": handle,
        "handle": handle,  # explicit handle claim (fusion unify P3, one-token story)
        "uid": str(user_id) if user_id is not None else None,
        "type": _TYPE,
        "iat": now,
        "exp": now + (ttl_s if ttl_s is not None else settings.auth_token_ttl_s),
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALG)


def verify_session_token(token: str) -> TokenClaims | None:
    """Decode + verify a session token. Returns its claims, or ``None`` for any
    invalid/expired/wrong-type token — the caller turns that into 401 or a
    handle-fallback, never a trusted actor."""
    if not token:
        return None
    try:
        decoded = jwt.decode(token, _SECRET, algorithms=[_ALG])
    except jwt.PyJWTError:
        return None
    if decoded.get("type") != _TYPE or not decoded.get("sub"):
        return None
    return TokenClaims(
        sub=str(decoded["sub"]),
        handle=(str(decoded["handle"]) if decoded.get("handle") else None),
        uid=(str(decoded["uid"]) if decoded.get("uid") else None),
        type=_TYPE,
    )
