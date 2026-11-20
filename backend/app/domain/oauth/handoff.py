"""An authorization the browser came back with, held until its starter claims it.

A third-party authorization (Feishu for a person's connection, a remote MCP
server for a project) used to be saved by its callback: whoever the
authorization server sent back to it, the tokens went where the state said.
The state proves which flow it is, not whose browser finishes it — so a flow
one person started could be finished by somebody else: send them the
authorization link, they approve with their own account, and *their* tokens
land on the starter's integration or project.

So the callback saves nothing. It keeps what came back under a fresh, random
id for a few minutes and sends the browser to the page that started the flow,
naming that id. The page — signed in as whoever this browser belongs to —
claims it (`POST /oauth-handoffs/{id}`), and the claim finishes the flow only
for the person the flow names as its starter. Anyone else's claim spends the
hand-off and saves nothing.

The id travels only in this redirect, to this browser; it is single use
(GETDEL) and short-lived. Fail closed: without Valkey nothing is kept, and the
flow is refused rather than saved unclaimed.
"""

import json
import secrets
from typing import Any

from pydantic import BaseModel

from app.core.redis import get_redis_client
from app.core.single_use_state import SingleUseUnavailableError

_PREFIX = "cheese:oauth_handoff:"
#: Long enough to land on the page and claim it, short enough that a code the
#: authorization server issued is still good to exchange.
HANDOFF_TTL_S = 300


async def keep(kind: str, payload: dict[str, Any]) -> str:
    """Hold what the authorization server sent back; return the claim id."""
    client = get_redis_client()
    if client is None:
        # i18n-exempt: an outage caught by the route, never shown to a reader
        raise SingleUseUnavailableError("redis not configured (oauth handoff)")
    handoff = secrets.token_urlsafe(24)
    try:
        await client.set(
            _PREFIX + handoff,
            json.dumps({"kind": kind, **payload}),
            ex=HANDOFF_TTL_S,
            nx=True,
        )
    except Exception as exc:  # a transport failure is a refusal
        # i18n-exempt: an outage caught by the route, never shown to a reader
        raise SingleUseUnavailableError("redis unreachable (oauth handoff)") from exc
    return handoff


async def take(handoff: str) -> dict[str, Any] | None:
    """The hand-off, once: None when it is unknown, spent or expired."""
    client = get_redis_client()
    if client is None:
        # i18n-exempt: an outage caught by the route, never shown to a reader
        raise SingleUseUnavailableError("redis not configured (oauth handoff)")
    try:
        raw = await client.getdel(_PREFIX + handoff)
    except Exception as exc:
        # i18n-exempt: an outage caught by the route, never shown to a reader
        raise SingleUseUnavailableError("redis unreachable (oauth handoff)") from exc
    if raw is None:
        return None
    return json.loads(raw)


class HandoffOutcome(BaseModel):
    """What a claim answers: the outcome the landing page shows, as the query
    parameters the callback used to land with."""

    query: dict[str, str]
