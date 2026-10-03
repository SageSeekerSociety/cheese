"""Turns a backend never saw start, taken in when a page connects.

The broker (`runtime.InProcessBroker`) learns which turns are running from the
``turn_started`` frames it relays, and keeps that in this process's memory. A
deploy replaces the process — the next container comes up, then the original
is recreated — so a turn started before it, or on the other container while
both ran, is missing from the backend a page connects to next, though its agent
is still at work. The database has the turn's interval open
(`AgentTurnRepository.open_on`); this puts it back into the broker's books.
Here rather than on the broker because `runtime.py` is over its size cap.
"""

from collections.abc import Iterable

from app.domain.agent.runtime import InProcessBroker


def adopt(
    broker: InProcessBroker,
    channel: str,
    turns: Iterable[tuple[str, float, str | None]],
) -> None:
    """Take in ``(turn id, started at, agent seat)`` the broker does not know.
    One it already knows is left as it is. The ``turn_finished`` that ends an
    adopted turn ends it like any other."""
    for turn_id, started, agent in turns:
        if turn_id in broker._active.get(channel, ()):
            continue
        broker._active.setdefault(channel, set()).add(turn_id)
        broker._active_since.setdefault((channel, turn_id), started)
        if agent and (
            followed := broker.activity.turn_started(channel, turn_id, agent)
        ):
            broker._fan_out(channel, followed)
