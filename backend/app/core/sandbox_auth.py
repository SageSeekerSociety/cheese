"""Auth for the sandbox-facing API (the `cheese` CLI calls these endpoints).

The sandbox container reaches the backend over the network (host.docker.internal),
so the cheese write-endpoints must NOT be open like the browser-facing ones. Each
`cheese` call carries X-Cheese-Token; the gate lives in app.main.cheese_token_gate.

Two token kinds (review R5):
- **Scoped per-turn token** (the default, minted by the compute provider for each
  turn): an HMAC over {project, topic, exp, acting agent}. The gate verifies the
  signature AND
  that the project/topic in the request URL matches the token's claims — so a
  container for project A literally cannot write project B, and the grant expires.
- **Global SANDBOX_TOKEN**: the signing secret, also accepted directly as a dev /
  trusted-single-host override. Drop this acceptance once untrusted (remote /
  competition) nodes exist — see design v2 R5/R6.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

from app.core.config import settings
from app.domain.identity.handles import topic_agent_handle

# Signing secret (per-process unless pinned via SANDBOX_TOKEN). Never sent to a
# container as-is when scoped tokens are used; it only signs them.
SANDBOX_TOKEN: str = settings.sandbox_token or secrets.token_hex(24)
_SECRET = SANDBOX_TOKEN.encode()

# Default scoped-token lifetime — a turn is minutes; this is a safe ceiling.
_SCOPED_TTL_S = 3600


def _sign(body: str) -> str:
    digest = hmac.new(_SECRET, body.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def mint_scoped_token(
    *,
    project_id: str,
    topic_id: str | None = None,
    ttl_s: int = _SCOPED_TTL_S,
    agent_handle: str | None = None,
) -> str:
    """Mint an HMAC token scoped to a project (+ optional topic), expiring in ttl_s.

    The token also names WHO acts with it (claim ``a``): the topic's 分身 agent
    handle, derived from the topic id unless the caller pins one. Without it a
    token said only "which topic" — every 分身 collapsed into the one platform
    ``cheese`` account, so nothing it did was attributable and revoking one meant
    waiting out the TTL. Tokens minted without a topic carry no identity claim
    (project-wide capabilities like the git-http/LLM proxies act as the platform).
    """
    payload: dict[str, str | int | None] = {
        "p": project_id,
        "t": topic_id,
        "exp": int(time.time()) + ttl_s,
    }
    actor = agent_handle or (topic_agent_handle(topic_id) if topic_id else None)
    if actor:
        payload["a"] = actor
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{body}.{_sign(body)}"


def token_agent_handle(token: str) -> str | None:
    """The 分身 handle a VALID scoped token acts as, or ``None``.

    ``None`` covers both a bad/expired token and a legitimately identity-less one
    (pre-upgrade tokens still in flight, project-scoped capability tokens) — the
    caller falls back to the platform agent, which is exactly the old behaviour.
    """
    claims = scoped_token_claims(token)
    if claims is None:
        return None
    actor = claims.get("a")
    return actor if isinstance(actor, str) and actor else None


def verify_scoped_token(
    token: str, *, project_id: str | None = None, topic_id: str | None = None
) -> bool:
    """True iff `token` is a valid scoped token (good signature, unexpired) whose
    claims match the given project_id / topic_id (whichever are provided)."""
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(sig, _sign(body)):
        return False
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return False
    if project_id is not None and payload.get("p") != project_id:
        return False
    if topic_id is not None and payload.get("t") != topic_id:
        return False
    return True


def scoped_token_claims(token: str) -> dict | None:
    """The {p, t, exp} claims of a VALID scoped token, else None. Lets a route
    resolve the token's project without a DB lookup (signature + expiry are
    verified; resource matching stays the caller's job)."""
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return None
    return payload


def is_global_sandbox_token(token: str) -> bool:
    """True for the signing secret used directly (dev / trusted-single-host
    override). It carries NO project scope, so a caller accepting it must get the
    room from somewhere else — see the note on `is_valid_cheese_token`."""
    return bool(token) and secrets.compare_digest(token, SANDBOX_TOKEN)


def is_valid_cheese_token(
    token: str, *, project_id: str | None = None, topic_id: str | None = None
) -> bool:
    """Gate check: a scoped token matching the request's resource, or the global
    secret (dev / trusted-single-host override)."""
    if project_id is None and topic_id is None:
        return False
    if verify_scoped_token(token, project_id=project_id, topic_id=topic_id):
        return True
    return secrets.compare_digest(token, SANDBOX_TOKEN)
