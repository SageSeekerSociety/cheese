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
async def test_adopted_thread_work_notifies_the_parent_room():
    """The main room hears when a thread's recovered turn starts and stops."""
    broker = InProcessBroker()
    broker.activity.note_thread("thread", "room")
    async with broker.subscribe("room") as room:
        broker.adopt("thread", [("turn", 123.0, "cheese")], read_at=broker.books_read())
        assert not room.empty(), "The parent room was not told the thread is working"
        assert room.get_nowait() == {
            "type": "thread_activity",
            "thread_id": "thread",
            "member": "cheese",
            "active": True,
        }

        await broker.publish("thread", {"type": "turn_finished", "turn_id": "turn"})
        assert room.get_nowait() == {
            "type": "thread_activity",
            "thread_id": "thread",
            "member": "cheese",
            "active": False,
        }
        assert room.empty()
