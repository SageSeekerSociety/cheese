"""Agent handle naming — pure, dependency-free, importable from anywhere.

Split out of ``app.domain.identity.services`` so the token minter
(``app.core.sandbox_auth``) can name the acting 分身 without importing the DB
layer. Nothing here touches a session; the authoritative "is this an agent?" answer
is still ``IdentityService.is_agent`` (the ``AgentBinding``).
"""

import uuid

# 芝士's platform-wide handle — a real user row, seeded once, and the handle a
# project's default agent keys its memory under.
#
# It used to double as the fallback identity ("a token that names no 分身"), and
# that was one job too many: once an agent's memory is keyed by its handle, every
# call the platform could not attribute wrote into the DEFAULT agent's pool. The
# unattributable case now has a name of its own, below.
CHEESE_HANDLE = "cheese"
CHEESE_NAME = "芝士"

# What a credential that names no 分身 resolves to when there is no project to
# ask either — i.e. genuinely "we cannot tell which agent this is". Inside the
# `cheese-` namespace on purpose: it is reserved against human registration and
# renders as an agent by the same house rule as every other 分身 handle.
UNRESOLVED_AGENT_HANDLE = "cheese-unresolved"

# Namespace for per-topic 分身 handles. Derived, never stored as a lookup table:
# the handle is a pure function of the topic id, so a token can carry the acting
# identity without a DB round-trip at mint time.
TOPIC_AGENT_PREFIX = "cheese-"

# How much of the topic uuid goes into the handle. 12 hex chars = 48 bits; the
# handle stays readable and collisions are not a practical concern.
_TOPIC_AGENT_HEX = 12


def topic_agent_handle(topic_id: uuid.UUID | str) -> str:
    """The handle the 分身 of ``topic_id`` acts under. Pure and deterministic."""
    hexed = (
        topic_id.hex
        if isinstance(topic_id, uuid.UUID)
        else str(topic_id).replace("-", "")
    )
    return f"{TOPIC_AGENT_PREFIX}{hexed[:_TOPIC_AGENT_HEX]}"


# How a 私聊 with an AI teammate is addressed — in the URL the browser shows and
# in the unread map keyed by "who am I talking to". Prefixed rather than bare,
# because a teammate's handle is chosen per project (``AgentInstance.handle``)
# and nothing stops someone naming one after a person on the roster; without the
# prefix, that person's DM and that teammate's DM would be the same string.
DM_AGENT_PREFIX = "agent:"


def agent_dm_key(agent_handle: str) -> str:
    """How the UI addresses the 私聊 with this teammate. Pure."""
    return f"{DM_AGENT_PREFIX}{agent_handle}"


def looks_like_agent_handle(handle: str) -> bool:
    """Whether ``handle`` belongs to 芝士 (the platform row or any topic 分身).

    A *display / house-rule* helper for paths that have no session to derive the
    real ``AgentBinding`` from — e.g. rendering an event line. Authorization must
    NEVER use it: ``IdentityService.is_agent`` (the binding) is the truth.
    """
    return handle == CHEESE_HANDLE or handle.startswith(TOPIC_AGENT_PREFIX)


# What a caller with NO credential resolves to (``app.api.auth``). A literal, not
# None — so it is a string a human could otherwise have registered, which is the
# whole reason the next function exists.
ANONYMOUS_HANDLE = "anonymous"

# The author handle the platform posts its own notices under (deploy warnings,
# gate verdicts, auto-resume lines). Same hazard as the sentinel: a human wearing
# this name would read as the platform speaking.
SYSTEM_HANDLE = "system"


def is_reserved_username(username: str) -> bool:
    """Whether a *human* is forbidden from registering under this name (#345).

    The platform already uses these strings to mean something other than "a
    person": ``anonymous`` means "I don't know who you are", ``system`` prefixes
    the platform's own notices, and ``cheese``/``cheese-<topic>`` are 芝士 and
    her per-topic 分身. If a real account could hold one, then every unattributed
    log line, every ``author='anonymous'`` message and every system notice starts
    reading like that person said it — and authz code that special-cases the
    string would be deciding about a real user.

    This is the narrow half of #345: stop new collisions. It deliberately does
    NOT try to replace the sentinel with ``None`` (the broad half — a dozen call
    sites in authz and authorship read the literal), and it deliberately does not
    apply to 芝士's own rows: ``IdentityService._create_agent_user`` goes through
    the repository, not through registration, and must keep being able to create
    ``cheese`` and ``cheese-<topic>``.

    Case-folded on purpose. ``Anonymous`` would not collide with the sentinel in
    code, but it collides in the only place that matters for the second half of
    the harm — a human reading the room and deciding who said what.
    """
    folded = username.strip().casefold()
    return (
        folded in {ANONYMOUS_HANDLE, SYSTEM_HANDLE}
        # `looks_like_agent_handle` is the same house rule used for display, so
        # the two cannot drift apart into "reserved but renderable as a person".
        or looks_like_agent_handle(folded)
    )


def names_a_person(handle: str | None) -> bool:
    """Whether ``handle`` names a HUMAN, as opposed to nobody or the platform.

    Attribution needs this: a handle that reaches it may be a real person, the
    ``anonymous`` sentinel, ``system`` (every platform-initiated turn — gate
    verdicts, scheduled wake-ups), or 芝士 / one of her
    per-topic 分身. Only the first may become a topic's owner or be credited on
    a commit; the rest must fall through to whatever the caller's fallback is.

    Defined as the complement of :func:`is_reserved_username` on purpose — those
    reserved strings are reserved *precisely because* they do not name a person,
    so deriving one from the other keeps a new sentinel from being handled in one
    place and forgotten in the other.
    """
    return bool(handle and handle.strip() and not is_reserved_username(handle))
