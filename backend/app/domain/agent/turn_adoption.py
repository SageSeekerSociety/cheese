"""Turns a backend never saw start, taken in when a page connects.

The broker (`realtime.broker.InProcessBroker`) learns which turns are running from the
``turn_started`` frames it relays, and keeps that in this process's memory. A
deploy replaces the process — the next container comes up, then the original
is recreated — so a turn started before it, or on the other container while
both ran, is missing from the backend a page connects to next, though its agent
is still at work. The database has the turn's interval open
(`AgentTurnRepository.open_on`); this puts it back into the broker's books.
Database reads stay here; the realtime broker owns only the in-memory adoption.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.repositories import AgentTurnRepository


async def open_turns_on(
    session: AsyncSession, conversation_id: uuid.UUID
) -> list[tuple[str, float, str | None]]:
    """The delivered turns still open in the conversation."""
    return await AgentTurnRepository(session).open_on(conversation_id)


def adopt(
    broker: InProcessBroker,
    channel: str,
    turns: Iterable[tuple[str, float, str | None]],
) -> None:
    """Take in ``(turn id, started at, agent seat)`` the broker does not know.
    One it already knows is left as it is. The ``turn_finished`` that ends an
    adopted turn ends it like any other."""
    broker.adopt(channel, turns)
