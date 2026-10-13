"""Per-conversation death evidence, and the sweep's third answer (FB-56
legacy③): a conversation is dead only on an authority's own terminal answer
bound to its stored resume token; everything else is unknown, and unknown
stays open. One home for the predicate and the refresh, so neither Chat nor
the runner grows the rules inline.
"""

import uuid

from sqlalchemy import select

from app.domain.agent.turn.state.live import LiveWork
from app.domain.agent_session.models import AgentSession


def unknown_row(chat_service, record) -> bool:
    """Should this open interval stay open because nothing says its
    conversation died? A row without a delivery stamp follows the separate
    dispatch/retry policy instead. A chat service that cannot answer
    ``row_is_dead`` at all is missing the observation itself — and missing
    means unknown, never the historical "dead".
    """
    if not record.delivered:
        return False
    row_dead = getattr(chat_service, "row_is_dead", None)
    if row_dead is None:
        return True
    return not row_dead(record.topic_id, record.agent_handle, record.session_id)


async def refresh(session, compute, live: LiveWork) -> None:
    """Fold one recover round's per-conversation evidence into the durable
    set. A conversation that answered the round's ping is alive — and any
    older death record for exactly it is revoked, or the rows it starts next
    would close on a corpse that is not one. A conversation the authority's
    own terminal answer names, bound to exactly the stored resume token, is
    dead. Everything else — offline, a call error, a timeout, a missing
    field, another conversation's success — is unknown, and unknown is left
    out: zero recovered sessions is exactly the case this still runs for.
    """
    pointers = (
        (
            await session.execute(
                select(AgentSession).where(AgentSession.resume_token.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    for pointer in pointers:
        key: tuple[uuid.UUID, str, str] = (
            pointer.conversation_id,
            pointer.agent_handle,
            pointer.resume_token,
        )
        if pointer.resume_token in compute.found_conversations(
            pointer.conversation_id, pointer.agent_handle
        ):
            live.dead_sessions.discard(key)
            continue
        if pointer.resume_token in compute.terminal_conversations(
            pointer.conversation_id, pointer.agent_handle
        ):
            live.dead_sessions.add(key)


def row_is_dead(
    live: LiveWork,
    compute,
    topic_id: uuid.UUID,
    agent_handle: str,
    session_id: str | None,
) -> bool:
    """Is THIS row's conversation known dead? Matched by the row's own
    session id only — a row without one is not attributed by anything else,
    and stays "unknown" by construction.
    """
    if session_id is None:
        return False
    if (topic_id, agent_handle, session_id) in live.dead_sessions:
        return True
    return session_id in compute.dead_conversations(topic_id, agent_handle)


def seat_state(live: LiveWork, compute, topic_id: uuid.UUID, agent_handle: str) -> str:
    """One of "live" / "dead" / "unknown" for the seat: a session nobody has
    seen die and nobody holds is not a dead one.
    """
    return compute.seat_state(topic_id, agent_handle)
