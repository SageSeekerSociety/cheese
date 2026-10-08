"""Who a room says is working: one mark per agent that has a turn running here."""

import pytest

from app.domain.agent.realtime.broker import InProcessBroker


def marks(live) -> list[tuple[str, bool]]:
    """The activity marks this subscriber has been handed, in order."""
    told: list[tuple[str, bool]] = []
    while not live.empty():
        frame = live.get_nowait()
        if frame["type"] == "activity":
            told.append((frame["member"], frame["active"]))
    return told


@pytest.mark.anyio
async def test_a_turn_answered_for_by_another_handle_stops_the_first_one():
    """One turn belongs to one agent at a time, and a turn's attribution can
    change while it runs: a frame arriving before the turn's books are open
    falls back to the session's own handle, and the ones after it carry the seat
    it authors under. Whoever the turn no longer belongs to has no turn left
    here, so the room must stop drawing it as working — nothing else retires
    that mark, and a reconnect would not list it either, so it would stay
    drawn until the page is reloaded."""
    broker = InProcessBroker()
    async with broker.subscribe("room") as live:
        await broker.publish(
            "room", {"type": "turn_started", "turn_id": "turn", "agent": "cheese-key"}
        )
        assert marks(live) == [("cheese-key", True)]

        await broker.publish(
            "room", {"type": "turn_started", "turn_id": "turn", "agent": "cheese-seat"}
        )
        assert set(marks(live)) == {("cheese-seat", True), ("cheese-key", False)}
        assert [e["member"] for e in broker.activity.snapshot("room")] == [
            "cheese-seat"
        ]

        # And the turn ending retires the handle it is attributed to now, not
        # the one it started under.
        await broker.publish("room", {"type": "turn_finished", "turn_id": "turn"})
        assert marks(live) == [("cheese-seat", False)]
        assert broker.activity.snapshot("room") == []


@pytest.mark.anyio
async def test_a_handle_holding_another_live_turn_goes_on_working():
    """One agent can hold several turns in a room and stops working when the
    last of them ends, so re-attributing one of its turns unmarks nothing."""
    broker = InProcessBroker()
    async with broker.subscribe("room") as live:
        for turn in ("one", "two"):
            await broker.publish(
                "room",
                {"type": "turn_started", "turn_id": turn, "agent": "cheese-key"},
            )
        assert marks(live) == [("cheese-key", True)]

        await broker.publish(
            "room", {"type": "turn_started", "turn_id": "one", "agent": "cheese-seat"}
        )
        assert marks(live) == [("cheese-seat", True)]
        assert {e["member"] for e in broker.activity.snapshot("room")} == {
            "cheese-key",
            "cheese-seat",
        }
