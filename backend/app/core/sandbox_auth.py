"""Auth for the sandbox-facing API (the `cheese` CLI calls these endpoints).

The sandbox container reaches the backend over the network (host.docker.internal),
so the cheese write-endpoints must NOT be open like the browser-facing ones. Each
`cheese` call carries its credential as ``Authorization: Bearer``; the routes only 芝士 may call declare the
gate (CHEESE_ONLY_IN_ROOM / CHEESE_ONLY_IN_PROJECT in app/api/write_access.py).

Three token kinds (review R5), and a fourth outside rooms altogether — the
**personal credential** a person's 芝士 holds (below), which opens nothing the
other three open:
- **Scoped per-turn token** (the default, minted by the compute provider for each
  turn): an ``agent+jwt`` over {project, topic, exp, acting agent}. The gate verifies the
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

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from app.core import credentials
from app.core.config import settings
from app.core.credentials import Kind

# Signing secret. Stable across restarts by construction — pinned via
# SANDBOX_TOKEN, else derived from jwt_secret; see
# `Settings.sandbox_signing_secret` for why a per-process random was a live
# outage rather than a hardening measure. Never sent to a container as-is when
# scoped tokens are used; the keys that sign them are derived from it
# (`app.core.credentials`).
SANDBOX_TOKEN: str = settings.sandbox_signing_secret

# Default scoped-token lifetime — a turn is minutes; this is a safe ceiling.
_SCOPED_TTL_S = 3600


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
    """Mint an agent credential scoped to a project (+ optional conversation), expiring
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
    # ``iat`` (set by `credentials.mint`) tells two credentials for the same
    # agent in the same room apart by age: the newer one is the launch that is
    # current (the preview tunnel keeps the helper holding it).
    payload: dict[str, str | int | None] = {"p": project_id, "t": topic_id}
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
    return credentials.mint(Kind.AGENT, payload, ttl_s=ttl_s)


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

    ``scratch`` makes it one whose work is not kept: a 支线's. On a machine
    that is its session's own it does anything there but carry the work into
    the project; on one shared with others it only reads
    (``routes/execution.py``). It carries ``ro`` as well:
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
    return credentials.mint(Kind.AGENT, claims)


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


#: The claim that says an execution machine holds the credential
#: (`for_a_machine`).
_MACHINE_CLAIM = "held"


def for_a_machine(token: str) -> str:
    """``token``, re-issued as the copy an execution machine is handed: the
    same claims, marked as held there. The session host's own copy of the
    same credential is not marked; it is what Codex and Pi call the model
    route with, and it never leaves the platform's machine."""
    claims = scoped_token_claims(token)
    if claims is None:
        raise ValueError(
            "A valid scoped credential is required"  # i18n-exempt: platform bug
        )
    claims[_MACHINE_CLAIM] = "machine"
    return credentials.mint(Kind.AGENT, claims)


def held_by_a_machine(claims: dict) -> bool:
    """Whether a verified scoped credential is the copy an execution machine
    was handed (`for_a_machine`). The model routes refuse it: a sandbox asks
    for a model on its platform socket, where the platform knows whose it is
    without a token (``/machine/llm``), and a copy of this one taken anywhere
    else would otherwise be a key to the project's model pool for as long as
    it lives. Being bound to a session says nothing of where a credential is:
    the session host's harness holds a session-bound one too."""
    return claims.get(_MACHINE_CLAIM) == "machine"


def scoped_token_claims(token: str) -> dict | None:
    """The {p, t, exp} claims of a VALID scoped token, else None. Lets a route
    resolve the token's project without a DB lookup (signature + expiry are
    verified; resource matching stays the caller's job)."""
    return credentials.read(token, Kind.AGENT)


# --- Project agent credential -------------------------------------------------
#
# 芝士 is not a person's account and not a person's delegate: it is a participant
# of the project, with its own context and its own mistakes. What it lacked was
# not an identity (``CHEESE_HANDLE`` has always existed) but a credential that
# outlives the process that minted it — a per-turn scoped token dies with the
# turn, so an agent running off-box had nothing to hold.
#
# So: a kind of its own, widened on both axes the per-turn one is too narrow on.
# One project instead of one topic; days instead of one turn. Revocation stays
# stateless — the credential names the project's credential *generation* (epoch),
# the project stores the current one, and bumping it retires every credential
# issued so far without a row to delete or a table to add.

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
    """Cheap test of the kind a token says it is, so ordinary traffic never
    pays for a verification (or, downstream, for the database read that
    checking the epoch costs)."""
    return credentials.kind_of(token) is Kind.PROJECT_AGENT


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
    return credentials.mint(
        Kind.PROJECT_AGENT, {"p": project_id, "e": epoch}, ttl_s=ttl_s
    )


def project_agent_claims(token: str) -> ProjectAgentClaims | None:
    """The claims of a well-formed, correctly-signed, unexpired credential — else
    ``None``."""
    payload = credentials.read(token, Kind.PROJECT_AGENT)
    if payload is None:
        return None
    project_id, epoch = payload.get("p"), payload.get("e")
    if not isinstance(project_id, str) or not project_id:
        return None
    if not isinstance(epoch, int) or isinstance(epoch, bool):
        return None
    return ProjectAgentClaims(
        project_id=project_id,
        epoch=epoch,
        expires_at=datetime.fromtimestamp(payload["exp"], UTC),
    )


# --- Personal credential --------------------------------------------------------
#
# A person's 芝士 runs on the session host like a room's does, and reaches the
# platform for two things: the model, and the few tools that read what that
# person may see. What it holds for that names a person and one of their
# conversations, and nothing of any project: no project, room or agent claim that
# a room's endpoint could match. Being a kind of its own makes it no credential
# at all to every path that reads the other kinds, and the paths that take it
# take nothing else.
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


def _person_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def mint_personal_credential(
    *, user_id: int, conversation_id: str, ttl_s: int = PERSONAL_CREDENTIAL_TTL_S
) -> str:
    return credentials.mint(
        Kind.PERSONAL, {"u": user_id, "c": conversation_id}, ttl_s=ttl_s
    )


def personal_claims(token: str) -> PersonalClaims | None:
    """The claims of a well-formed, correctly-signed, unexpired personal
    credential, else ``None``."""
    payload = credentials.read(token, Kind.PERSONAL)
    if payload is None:
        return None
    user_id, conversation = payload.get("u"), payload.get("c")
    if not _person_id(user_id):
        return None
    if not isinstance(conversation, str) or not conversation:
        return None
    return PersonalClaims(user_id=user_id, conversation_id=conversation)  # type: ignore[arg-type]


# What a person's 芝士 carries into its own sandbox (``routes/execution.py``):
# whose 芝士, which conversation's sandbox, which session and which of its
# leases, as ``bind_resource_token`` says them for a room's. It opens that
# sandbox and nothing else.


def mint_personal_execution_credential(
    *,
    user_id: int,
    conversation_id: str,
    session_id: str,
    lease_generation: str,
    ttl_s: int = PERSONAL_CREDENTIAL_TTL_S,
) -> str:
    return credentials.mint(
        Kind.PERSONAL_EXECUTION,
        {
            "u": user_id,
            "t": conversation_id,
            "r": conversation_id,
            "session": session_id,
            "lease": lease_generation,
        },
        ttl_s=ttl_s,
    )


def personal_execution_claims(token: str) -> dict | None:
    """The claims of a well-formed, correctly-signed, unexpired personal
    execution credential, else ``None``."""
    payload = credentials.read(token, Kind.PERSONAL_EXECUTION)
    if payload is None or not _person_id(payload.get("u")):
        return None
    if not all(isinstance(payload.get(k), str) for k in ("t", "r", "session", "lease")):
        return None
    return payload


# A 芝士 answering someone's question acts for that person: what it reads is
# judged by what the asker may read, and what it changes is recorded as done by
# the agent at the asker's request. The credential is minted for one question
# and lives no longer than the answer may, and it opens only the routes that
# declare they accept it (`api.auth.DELEGATED_ROUTES`).


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
    #: Whether the answer is shown to every reader of the document, not only
    #: the asker: then it reads only what all of them may.
    shown_to_readers: bool = False
    #: A person's own 芝士: the conversation the question was asked in, whose
    #: words its gate reads (`assistant.gate`).
    conversation: str | None = None


def _delegated_payload(claims: DelegatedClaims) -> dict:
    return {
        **({"c": claims.conversation} if claims.conversation else {}),
        "u": claims.user_id,
        "h": claims.handle,
        "a": claims.agent,
        "p": claims.project_id,
        "t": claims.topic_id,
        "w": claims.work,
        "ro": claims.read_only,
        "sh": claims.shown_to_readers,
    }


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
    shown_to_readers: bool = False,
    conversation: str | None = None,
) -> str:
    claims = DelegatedClaims(
        user_id=user_id,
        handle=handle,
        agent=agent,
        project_id=project_id,
        topic_id=topic_id,
        work=work,
        read_only=read_only,
        shown_to_readers=shown_to_readers,
        conversation=conversation,
    )
    return credentials.mint(Kind.DELEGATED, _delegated_payload(claims), ttl_s=ttl_s)


def looks_like_delegated_credential(token: str) -> bool:
    """A delegated credential, or a pass (`mint_pass`), which acts for the
    same person through the one door it was issued for."""
    return credentials.kind_of(token) in (Kind.DELEGATED, Kind.PASS)


def _delegated(payload: dict | None) -> DelegatedClaims | None:
    if payload is None:
        return None
    user_id, handle, work = payload.get("u"), payload.get("h"), payload.get("w")
    if user_id is not None and not _person_id(user_id):
        return None
    if not isinstance(handle, str) or not handle:
        return None
    if not isinstance(work, str) or not work:
        return None
    project, topic, agent = payload.get("p"), payload.get("t"), payload.get("a")
    if not all(
        v is None or (isinstance(v, str) and v) for v in (project, topic, agent)
    ):
        return None
    if topic is not None and project is None:
        return None
    return DelegatedClaims(
        user_id=user_id,  # type: ignore[arg-type]
        handle=handle,
        agent=agent,  # type: ignore[arg-type]
        project_id=project,  # type: ignore[arg-type]
        topic_id=topic,  # type: ignore[arg-type]
        work=work,
        read_only=payload.get("ro") is not False,
        shown_to_readers=payload.get("sh") is True,
        conversation=payload["c"] if isinstance(payload.get("c"), str) else None,
    )


def delegated_claims(token: str) -> DelegatedClaims | None:
    """The claims of a well-formed, correctly-signed, unexpired delegated
    credential or pass, else ``None`` — never an exception."""
    kind = credentials.kind_of(token)
    if kind not in (Kind.DELEGATED, Kind.PASS):
        return None
    return _delegated(credentials.read(token, kind))


# --- Pass ---------------------------------------------------------------------
#
# What a person's 芝士 shows at a door its gate opened (`assistant.passes`): the
# delegated credential's claims, for the few calls the gate let through, once
# each. It travels alone, in place of the delegated credential, so a request
# carries one credential however it was let in; which doors it opens and
# whether they are used up is kept under its ``jti``.


def mint_pass(claims: DelegatedClaims, *, jti: str, ttl_s: int) -> str:
    return credentials.mint(
        Kind.PASS, {**_delegated_payload(claims), "jti": jti}, ttl_s=ttl_s
    )


def pass_id(token: str) -> str | None:
    """The ``jti`` of a valid, unexpired pass, else None."""
    payload = credentials.read(token, Kind.PASS)
    jti = payload.get("jti") if payload else None
    return jti if isinstance(jti, str) and jti else None


def is_global_sandbox_token(token: str) -> bool:
    """True for the signing secret used directly (dev / trusted-single-host
    override). It carries NO project scope, so a caller accepting it must get the
    room from somewhere else — see the note on `is_valid_cheese_token`."""
    return credentials.is_platform_secret(token)


def is_valid_cheese_token(
    token: str, *, project_id: str | None = None, topic_id: str | None = None
) -> bool:
    """Gate check: a scoped token matching the request's resource, or the global
    secret (dev / trusted-single-host override)."""
    if project_id is None and topic_id is None:
        return False
    if verify_scoped_token(token, project_id=project_id, topic_id=topic_id):
        return True
    return is_global_sandbox_token(token)
