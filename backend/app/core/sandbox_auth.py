"""Auth for the sandbox-facing API (the `cheese` CLI calls these endpoints).

The sandbox container reaches the backend over the network (host.docker.internal),
so the cheese write-endpoints must NOT be open like the browser-facing ones. Each
`cheese` call carries X-Cheese-Token; the gate lives in app.main.cheese_token_gate.

Three token kinds (review R5):
- **Scoped per-turn token** (the default, minted by the compute provider for each
  turn): an HMAC over {project, topic, exp, acting agent}. The gate verifies the
  signature AND
  that the project/topic in the request URL matches the token's claims — so a
  container for project A literally cannot write project B, and the grant expires.
- **Project agent credential** (below): the same construction widened to a whole
  project and to days instead of one turn, so an agent running OUTSIDE the
  platform process can hold one. A per-turn token cannot leave the turn that
  minted it, which is why nothing off-box could act as 芝士 before.
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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from app.core.config import settings
from app.domain.identity.handles import topic_agent_handle

# Signing secret. Stable across restarts by construction — pinned via
# SANDBOX_TOKEN, else derived from jwt_secret; see
# `Settings.sandbox_signing_secret` for why a per-process random was a live
# outage rather than a hardening measure. Never sent to a container as-is when
# scoped tokens are used; it only signs them.
SANDBOX_TOKEN: str = settings.sandbox_signing_secret
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
    access_scope: Literal["topic", "project"] = "topic",
    remote_control: bool = False,
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
    if access_scope == "project":
        if not actor:
            raise ValueError("Project participant access requires an agent identity")
        # The origin topic still binds execution callbacks. Collaboration routes
        # may use project scope only after checking this actor's actual roles.
        payload["s"] = "project"
    if actor:
        payload["a"] = actor
    if remote_control:
        if not topic_id:
            raise ValueError("RC credentials require a place")
        payload["rc"] = 1
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


# --- Project agent credential -------------------------------------------------
#
# 芝士 is not a person's account and not a person's delegate: it is a participant
# of the project, with its own context and its own mistakes. What it lacked was
# not an identity (``CHEESE_HANDLE`` has always existed) but a credential that
# outlives the process that minted it — a per-turn scoped token dies with the
# turn, so an agent running off-box had nothing to hold.
#
# So: same HMAC construction as above, widened on both axes it was too narrow on.
# One project instead of one topic; days instead of one turn. Revocation stays
# stateless — the credential names the project's credential *generation* (epoch),
# the project stores the current one, and bumping it retires every credential
# issued so far without a row to delete or a table to add.
#
# The ``cxpa_`` prefix is a hard separator, not decoration. The signature covers a
# different domain string, so a project credential can never verify as a scoped
# token nor a scoped token as a project credential — every existing scoped-token
# path sees one of these as "no credential at all", which is what keeps this
# purely additive.
AGENT_CREDENTIAL_PREFIX = "cxpa_"
_AGENT_CREDENTIAL_DOMAIN = "cxpa1"

# Days, not minutes: the point of this credential is that a person can paste it
# into an agent's environment and forget about it for a while.
AGENT_CREDENTIAL_DEFAULT_TTL_DAYS = 90
AGENT_CREDENTIAL_MAX_TTL_DAYS = 365
_DAY_S = 86400


@dataclass(frozen=True)
class ProjectAgentClaims:
    """What a VALID project agent credential asserts. ``epoch`` still has to be
    checked against the project's current one — that check needs the database,
    so it lives in the domain service, not here."""

    project_id: str
    epoch: int
    expires_at: datetime


def looks_like_project_agent_credential(token: str) -> bool:
    """Cheap syntactic test, so ordinary traffic never pays for a verification
    (or, downstream, for the database read that checking the epoch costs)."""
    return token.startswith(AGENT_CREDENTIAL_PREFIX)


def mint_project_agent_credential(
    *,
    project_id: str,
    epoch: int,
    ttl_s: int = AGENT_CREDENTIAL_DEFAULT_TTL_DAYS * _DAY_S,
) -> str:
    """Mint a credential for ``project_id`` at credential generation ``epoch``.

    ``ttl_s`` is seconds rather than days because expiry is a security boundary
    that tests must be able to land on the far side of; the issuing service is
    what speaks in days.
    """
    payload: dict[str, str | int] = {
        "p": project_id,
        "e": epoch,
        "exp": int(time.time()) + ttl_s,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    signature = _sign(f"{_AGENT_CREDENTIAL_DOMAIN}.{body}")
    return f"{AGENT_CREDENTIAL_PREFIX}{body}.{signature}"


def project_agent_claims(token: str) -> ProjectAgentClaims | None:
    """The claims of a well-formed, correctly-signed, unexpired credential — else
    ``None``. Every malformed shape returns ``None`` rather than raising: this
    runs on attacker-controlled input on every request that carries the header."""
    if not looks_like_project_agent_credential(token):
        return None
    try:
        body, signature = token[len(AGENT_CREDENTIAL_PREFIX) :].split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(f"{_AGENT_CREDENTIAL_DOMAIN}.{body}")):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    project_id = payload.get("p")
    epoch = payload.get("e")
    expires = payload.get("exp")
    if not isinstance(project_id, str) or not project_id:
        return None
    if not isinstance(epoch, int) or isinstance(epoch, bool):
        return None
    if not isinstance(expires, int) or expires < time.time():
        return None
    return ProjectAgentClaims(
        project_id=project_id,
        epoch=epoch,
        expires_at=datetime.fromtimestamp(expires, UTC),
    )


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
