"""Agent handle naming — pure, dependency-free, importable from anywhere.

Split out of ``app.domain.identity.services`` so the token minter
(``app.core.sandbox_auth``) can name the acting 分身 without importing the DB
layer. Nothing here touches a session; the authoritative "is this an agent?" answer
is still ``IdentityService.is_agent`` (the ``AgentBinding``).
"""

import uuid

# 芝士's platform-wide handle — a real user row, seeded once. The fallback
# identity: what a token that names no 分身 resolves to.
CHEESE_HANDLE = "cheese"
CHEESE_NAME = "芝士"

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


def looks_like_agent_handle(handle: str) -> bool:
    """Whether ``handle`` belongs to 芝士 (the platform row or any topic 分身).

    A *display / house-rule* helper for paths that have no session to derive the
    real ``AgentBinding`` from — e.g. rendering an event line. Authorization must
    NEVER use it: ``IdentityService.is_agent`` (the binding) is the truth.
    """
    return handle == CHEESE_HANDLE or handle.startswith(TOPIC_AGENT_PREFIX)
