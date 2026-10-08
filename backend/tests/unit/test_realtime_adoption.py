"""Adopting a live turn restores attribution, not synthetic replay history."""

import pytest

from app.domain.agent.realtime.broker import InProcessBroker


@pytest.mark.anyio
async def test_adoption_keeps_the_first_start_and_agent_without_buffering():
    broker = InProcessBroker()
    async with broker.subscribe("room") as live:
        broker.adopt("room", [("turn", 123.0, "cheese-original")])
        assert live.get_nowait() == {
            "type": "activity",
            "member": "cheese-original",
            "kind": "working",
            "active": True,
            "since": 123.0,
        }
        broker.adopt("room", [("turn", 456.0, "cheese-other")])
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
