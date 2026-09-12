"""Session boundaries stay correct when acknowledgments race notifications."""

import pytest

from app.domain.agent.harness import Opening
from app.domain.agent.harness.codex import AppServerError, Session


class Peer:
    def __init__(self):
        self.requests = []
        self.adapter = Session(self)
        self.reject_steer = False
        self.complete_before_ack = False

    def notify(self, method, turn="turn"):
        self.adapter.observe(
            {
                "method": method,
                "params": {
                    "threadId": "thread",
                    "turn": {"id": turn},
                },
            }
        )

    async def request(self, method, params):
        self.requests.append((method, params))
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": "thread", "turns": []}}
        if method == "turn/start":
            self.notify("turn/started")
            if self.complete_before_ack:
                self.notify("turn/completed")
            return {"turn": {"id": "turn"}}
        if method == "turn/steer":
            if self.reject_steer:
                raise AppServerError({"code": -1, "message": "turn changed"})
            return {"turnId": "turn"}
        if method == "turn/interrupt":
            self.notify("turn/completed")
            return {}
        raise AssertionError(method)


@pytest.mark.anyio
async def test_completed_before_ack_does_not_leave_a_running_turn():
    peer = Peer()
    peer.complete_before_ack = True
    session = peer.adapter
    await session.open(
        Opening("instructions", model="fixture-model"), cwd="/fixture", tools=[]
    )
    assert peer.requests[0][1]["developerInstructions"] == "instructions"
    assert peer.requests[0][1]["model"] == "fixture-model"
    await session.send("first")
    assert session.turn_id is None
    await session.send("second")
    assert [method for method, _ in peer.requests] == [
        "thread/start",
        "turn/start",
        "turn/start",
    ]


@pytest.mark.anyio
async def test_stale_steer_is_not_retried_as_a_new_turn():
    peer = Peer()
    session = peer.adapter
    await session.open(Opening("instructions"), cwd="/fixture", tools=[])
    await session.send("first")
    peer.reject_steer = True
    with pytest.raises(AppServerError, match="turn changed"):
        await session.send("steering")
    assert [method for method, _ in peer.requests] == [
        "thread/start",
        "turn/start",
        "turn/steer",
    ]
    assert peer.requests[-1][1]["expectedTurnId"] == "turn"


@pytest.mark.anyio
async def test_interrupt_keeps_thread_and_next_input_starts_a_turn():
    peer = Peer()
    session = peer.adapter
    assert await session.interrupt() is False
    await session.open(
        Opening("instructions", resume_token="thread"), cwd="/fixture", tools=[]
    )
    assert peer.requests[0][0] == "thread/resume"
    assert "dynamicTools" not in peer.requests[0][1]
    await session.send("first")
    peer.notify("turn/completed", turn="an-older-turn")
    assert session.turn_id == "turn"
    assert await session.interrupt() is True
    assert session.thread_id == "thread"
    assert session.turn_id is None
    await session.send("next")
    assert peer.requests[-1][0] == "turn/start"
