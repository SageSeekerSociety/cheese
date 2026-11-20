"""Adopting a live turn restores attribution, not synthetic replay history."""

import pytest

from app.domain.agent.realtime.broker import InProcessBroker


@pytest.mark.anyio
async def test_adoption_keeps_the_first_start_and_agent_without_buffering():
    broker = InProcessBroker()
    async with broker.subscribe("room") as live:
        broker.adopt(
            "room", [("turn", 123.0, "cheese-original")], read_at=broker.books_read()
        )
        assert live.get_nowait() == {
            "type": "activity",
            "member": "cheese-original",
            "kind": "working",
            "active": True,
            "since": 123.0,
        }
        broker.adopt(
            "room", [("turn", 456.0, "cheese-other")], read_at=broker.books_read()
        )
        assert live.empty()
        assert broker.active_turns_since("room") == {"turn": 123.0}
        assert broker.activity.turn_agents("room") == {"turn": "cheese-original"}
        assert broker.in_flight("room")
        async with broker.subscribe("room", replay=True) as reconnect:
            assert reconnect.empty()
            assert broker.activity.snapshot("room") == [
                {"member": "cheese-original", "kind": "working", "since": 123.0}
            ]
        await broker.publish("room", {"type": "turn_finished", "turn_id": "turn"})
        assert live.get_nowait() == {"type": "turn_finished", "turn_id": "turn"}
        stopped = live.get_nowait()
        assert stopped["type"] == "activity"
        assert stopped["member"] == "cheese-original"
        assert stopped["active"] is False
        assert live.empty()
    assert not broker.in_flight("room")
    assert broker.activity.snapshot("room") == []
    async with broker.subscribe("room", replay=True) as reconnect:
        assert reconnect.empty()


@pytest.mark.anyio
async def test_a_read_from_before_a_turn_ended_does_not_bring_it_back():
    """A page connects while a turn ends: its read of the open turns ran before
    the end was written, and is adopted after the end was relayed. The turn
    stays ended; nobody is shown working."""
    broker = InProcessBroker()
    await broker.publish(
        "room", {"type": "turn_started", "turn_id": "turn", "agent": "cheese"}
    )
    read_at = broker.books_read()
    still_open = [("turn", 123.0, "cheese")]
    await broker.publish("room", {"type": "turn_finished", "turn_id": "turn"})

    assert broker.adopt("room", still_open, read_at=read_at) == []
    assert broker.activity.snapshot("room") == []
    assert not broker.in_flight("room")

    # A read taken after the end that still finds the turn open is believed:
    # whoever is running it is not this process.
    assert broker.adopt("room", still_open, read_at=broker.books_read()) == [
        ("turn", "cheese")
    ]


@pytest.mark.anyio
async def test_a_replayed_run_record_says_it_is_older_than_a_later_read():
    """A page reconnects mid-turn: the room's state, read now, reaches it
    before the replay of a run record from earlier in the turn. The record
    carries its place in the stream, and the read says its own, so the page
    can tell the replay is older than the read and a record published after
    the read is newer."""
    broker = InProcessBroker()
    await broker.publish(
        "room", {"type": "turn_started", "turn_id": "turn", "agent": "cheese"}
    )
    await broker.publish("room", {"type": "run_record", "record": {"id": "old"}})
    read_at = broker.books_read()
    async with broker.subscribe("room", replay=True) as reconnect:
        await broker.publish("room", {"type": "run_record", "record": {"id": "new"}})
        frames = [reconnect.get_nowait() for _ in range(3)]
    old, new = (f for f in frames if f["type"] == "run_record")
    assert old["record"]["id"] == "old" and new["record"]["id"] == "new"
    assert old["stream"] == new["stream"] == broker.stream
    assert old["at"] <= read_at < new["at"]
