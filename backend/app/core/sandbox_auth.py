"""Auth for the sandbox-facing API (the `cheese` CLI calls these endpoints).

The sandbox container reaches the backend over the network (host.docker.internal),
so the cheese write-endpoints must NOT be open like the browser-facing ones. Each
`cheese` call carries X-Cheese-Token; the gate lives in app.main.cheese_token_gate.

Three token kinds (review R5), and a fourth outside rooms altogether — the
**personal credential** a person's 芝士 holds (below), which opens nothing the
other three open:
- **Scoped per-turn token** (the default, minted by the compute provider for each
  turn): an HMAC over {project, topic, exp, acting agent}. The gate verifies the
  signature AND
  that the project/topic in the request URL matches the token's claims — so a
  container for project A literally cannot write project B, and the grant expires.
- **Project agent credential** (below): the same construction widened to a whole
  project and to days instead of one turn, so an agent running OUTSIDE the
  platform process can hold one. A per-turn token cannot leave the turn that
  minted it, which is why nothing off-box could act as 芝士 before.
- **Delegated credential** (below): one question's, for the 芝士 answering
  it on someone's behalf, judged by the asker's permissions.
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
    resource_id: str | None = None,
    document_id: str | None = None,
) -> str:
    """Mint an HMAC token scoped to a project (+ optional conversation), expiring
    in ttl_s.

    ``topic_id`` (claim ``t``) names the conversation: a room, or one of its
    tasks. A task's session holds its task's id there, and so acts in that task
    and in no other conversation.

    The token also names WHO acts with it (claim ``a``): ``agent_handle``, the
    handle of the agent this turn runs as. Without it a token said only "which
    topic" — every agent collapsed into the one platform ``cheese`` account, so
    nothing it did was attributable and revoking one meant waiting out the TTL.

    The caller names that agent; nothing here derives one. A room is a
    collaboration space and may seat several agents, so a handle derived from
    the room would give two of them one name and one of them two names — and a
    token is precisely where that mistake is unrecoverable, because the name is
    signed into it.

    A token minted without an agent carries no identity claim, and what that
    means afterwards is decided by whether it also names a topic. Naming none
    either, it is a project-wide capability — the git-http and LLM proxies —
    and nobody can answer for it: the platform acting, not an agent. Naming a
    topic, it is a turn in that room with no teammate pinned, and the room's
    roster answers for it (``api.auth.ActorResolver.acting_agent``); the
    transcript drain mints one of these, and the endpoints it posts to verify
    the token directly rather than resolving an actor at all.
    """
    now = int(time.time())
    payload: dict[str, str | int | None] = {
        "p": project_id,
        "t": topic_id,
        # When it was issued, so two credentials for the same agent in the same
        # room can be told apart by age: the newer one is the launch that is
        # current (the preview tunnel keeps the helper holding it).
        "iat": now,
        "exp": now + ttl_s,
    }
    actor = agent_handle
    if access_scope == "project":
        if not actor:
            raise ValueError("Project participant access requires an agent identity")
        # The origin topic still binds execution callbacks. Collaboration routes
        # may use project scope only after checking this actor's actual roles.
        payload["s"] = "project"
    if actor:
        payload["a"] = actor
    if resource_id is not None:
        payload["r"] = resource_id
    # A document question's session: the project's own agent may answer it in a
    # room it does not sit in (`llm_proxy`).
    if document_id is not None:
        payload["d"] = document_id
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{body}.{_sign(body)}"


def bind_resource_token(
    token: str,
    resource_id: str,
    *,
    session_id: str | None = None,
    lease_generation: str | None = None,
    reading: bool = False,
    scratch: bool = False,
) -> str:
    """Bind an existing scoped launch credential to its allocated execution.

    ``reading`` makes it a credential that only reads the machine's files
    (``routes/execution.py``): a document's 芝士 looking at the room's work.

    ``scratch`` makes it one whose work is not kept: a 支线, or a task its
    owner has not started. On a machine that is its session's own it does
    anything there but carry the work into the project; on one shared with
    others it only reads (``routes/execution.py``). It carries ``ro`` as well:
    the executor route is served by the device connection's owner, which an
    app release leaves on its image, and an owner that predates ``scratch``
    reads only ``ro`` and keeps the session to reading until it is released."""
    claims = scoped_token_claims(token)
    if claims is None:
        raise ValueError("A valid scoped launch credential is required")
    claims["r"] = resource_id
    # Binding issues a new credential for a new place, so it is a new issue.
    claims["iat"] = int(time.time())
    if session_id is not None:
        claims["session"] = session_id
    if lease_generation is not None:
        claims["lease"] = lease_generation
    if reading or scratch:
        claims["ro"] = True
    if scratch:
        claims["scratch"] = True
    raw = json.dumps(claims, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    if "session" in claims:
        # Old owners must reject this capability, even at their legacy URL.
        # The signed prefix cannot be stripped to obtain an older credential.
        body = "cxss_" + body
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
    payload = scoped_token_claims(token)
    if payload is None:
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
    if body.startswith("cxss_"):
        body = body.removeprefix("cxss_")
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


# --- Personal credential --------------------------------------------------------
#
# A person's 芝士 runs on the session host like a room's does, and reaches the
# platform for two things: the model, and the few tools that read what that
# person may see. What it holds for that names a person and one of their
# conversations, and nothing of any project: no project, room or agent claim that
# a room's endpoint could match. Its own prefix and signing domain, as the
# project credential's, make it no credential at all to every path that reads
# the other kinds, and the paths that take it take nothing else.
PERSONAL_CREDENTIAL_PREFIX = "cxpu_"
_PERSONAL_CREDENTIAL_DOMAIN = "cxpu1"
#: A session holds its credential for as long as it runs, and one idle for a few
#: minutes exits; a week is far past any session's life.
PERSONAL_CREDENTIAL_TTL_S = 7 * _DAY_S


@dataclass(frozen=True)
class PersonalClaims:
    """What a VALID personal credential asserts: whose 芝士, in which
    conversation. That the conversation is still that person's is the caller's
    to check, against the database."""

    user_id: int
    conversation_id: str


def mint_personal_credential(
    *, user_id: int, conversation_id: str, ttl_s: int = PERSONAL_CREDENTIAL_TTL_S
) -> str:
    now = int(time.time())
    payload = {"u": user_id, "c": conversation_id, "iat": now, "exp": now + ttl_s}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    signature = _sign(f"{_PERSONAL_CREDENTIAL_DOMAIN}.{body}")
    return f"{PERSONAL_CREDENTIAL_PREFIX}{body}.{signature}"


def personal_claims(token: str) -> PersonalClaims | None:
    """The claims of a well-formed, correctly-signed, unexpired personal
    credential, else ``None`` — never an exception, for the same reason as
    ``project_agent_claims``."""
    if not token.startswith(PERSONAL_CREDENTIAL_PREFIX):
        return None
    try:
        body, signature = token[len(PERSONAL_CREDENTIAL_PREFIX) :].split(".", 1)
    except ValueError:
        return None
    expected = _sign(f"{_PERSONAL_CREDENTIAL_DOMAIN}.{body}")
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    user_id, conversation, expires = (
        payload.get("u"),
        payload.get("c"),
        payload.get("exp"),
    )
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        return None
    if not isinstance(conversation, str) or not conversation:
        return None
    if not isinstance(expires, int) or expires < time.time():
        return None
    return PersonalClaims(user_id=user_id, conversation_id=conversation)


# A 芝士 answering someone's question acts for that person: what it reads is
# judged by what the asker may read, and what it changes is recorded as done by
# the agent at the asker's request. The credential is minted for one question
# and lives no longer than the answer may, and it opens only the routes that
# declare they accept it (`api.auth.DELEGATED_ROUTES`). Its own prefix and
# signing domain make it no credential at all to every path that reads the
# other kinds.
DELEGATED_CREDENTIAL_PREFIX = "cxdg_"
_DELEGATED_CREDENTIAL_DOMAIN = "cxdg1"


@dataclass(frozen=True)
class DelegatedClaims:
    """What a VALID delegated credential asserts."""

    #: The person the question is from, whose permissions apply: their handle,
    #: and their account's id when the asker has one on record.
    user_id: int | None
    handle: str
    #: The agent answering, who authors what it changes; None for a person's
    #: own 芝士, which changes nothing.
    agent: str | None
    #: Where it may act: one project and one room, or (both None) nowhere but
    #: the routes that name neither.
    project_id: str | None
    topic_id: str | None
    #: The answer it was minted for: what it changes is noted under it.
    work: str
    #: A question that may only be answered changes nothing.
    read_only: bool


def mint_delegated_credential(
    *,
    user_id: int | None,
    handle: str,
    work: str,
    ttl_s: int,
    agent: str | None = None,
    project_id: str | None = None,
    topic_id: str | None = None,
    read_only: bool = True,
) -> str:
    now = int(time.time())
    payload = {
        "u": user_id,
        "h": handle,
        "a": agent,
        "p": project_id,
        "t": topic_id,
        "w": work,
        "ro": read_only,
        "iat": now,
        "exp": now + ttl_s,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    signature = _sign(f"{_DELEGATED_CREDENTIAL_DOMAIN}.{body}")
    return f"{DELEGATED_CREDENTIAL_PREFIX}{body}.{signature}"


def looks_like_delegated_credential(token: str) -> bool:
    return token.startswith(DELEGATED_CREDENTIAL_PREFIX)


def delegated_claims(token: str) -> DelegatedClaims | None:
    """The claims of a well-formed, correctly-signed, unexpired delegated
    credential, else ``None`` — never an exception."""
    if not looks_like_delegated_credential(token):
        return None
    try:
        body, signature = token[len(DELEGATED_CREDENTIAL_PREFIX) :].split(".", 1)
    except ValueError:
        return None
    expected = _sign(f"{_DELEGATED_CREDENTIAL_DOMAIN}.{body}")
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    user_id, handle = payload.get("u"), payload.get("h")
    expires, work = payload.get("exp"), payload.get("w")
    if user_id is not None and (
        not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0
    ):
        return None
    if not isinstance(handle, str) or not handle:
        return None
    if not isinstance(work, str) or not work:
        return None
    if not isinstance(expires, int) or expires < time.time():
        return None
    project, topic, agent = payload.get("p"), payload.get("t"), payload.get("a")
    if not all(
        v is None or (isinstance(v, str) and v) for v in (project, topic, agent)
    ):
        return None
    if topic is not None and project is None:
        return None
    return DelegatedClaims(
        user_id=user_id,
        handle=handle,
        agent=agent,
        project_id=project,
        topic_id=topic,
        work=work,
        read_only=payload.get("ro") is not False,
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
