"""An unreadable session must yield recovery capacity without claiming success."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.domain.agent import recovery
from app.domain.agent.harness import SessionRef
from app.domain.agent.recovery import SessionRecovery
from app.domain.agent.room.sessions import lifecycle

pytestmark = pytest.mark.anyio


class RecoveringChat(SessionRecovery):
    def __init__(self, compute):
        self._compute = compute
        self.live = SimpleNamespace(replays={}, hook_work={})
        self._replay_slots = asyncio.Semaphore(1)
        self.prewarm = SimpleNamespace(nudge=Mock())


async def test_an_unreadable_session_yields_its_recovery_slot_and_retries(monkeypatch):
    broken = SessionRef(uuid.uuid4(), uuid.uuid4(), "agent", harness="claude-code")
    healthy = SessionRef(
        broken.project_id, uuid.uuid4(), "agent", harness="claude-code"
    )
    available = asyncio.Event()
    healthy_read = asyncio.Event()
    nudged = Mock()
    attempts = 0

    class Compute:
        async def recover_sessions(self, device_id):
            return [broken, healthy]

        def work_in_flight(self, *args):
            return None

        async def replay(self, session):
            nonlocal attempts
            if session == broken:
                attempts += 1
                await available.wait()
            else:
                healthy_read.set()

    monkeypatch.setattr(recovery, "REPLAY_ATTEMPT_S", 0.01, raising=False)
    monkeypatch.setattr(recovery, "REPLAY_RETRY_S", 0.01, raising=False)
    monkeypatch.setattr(recovery, "nudge_messages", nudged)
    chat = RecoveringChat(Compute())
    try:
        assert await chat.recover_sessions() == 2
        await asyncio.wait_for(healthy_read.wait(), 0.5)
        assert chat.replaying(broken.conversation_id) is not None
        assert not any(
            call.args[1] == broken.conversation_id for call in nudged.call_args_list
        )
        available.set()
        await asyncio.wait_for(chat.replays_settled(), 0.5)
        assert attempts >= 2
        assert chat.replaying(broken.conversation_id) is None
        assert (
            sum(
                call.args[1] == broken.conversation_id for call in nudged.call_args_list
            )
            == 1
        )
    finally:
        tasks = list(chat.live.replays.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_retrying_replay_waits_for_an_existing_reader():
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "agent", harness="claude-code")
    seat = (session.conversation_id, session.agent_handle)
    reading = asyncio.create_task(asyncio.Event().wait())
    caught = asyncio.Event()
    svc = SimpleNamespace(
        _seat_of=lambda _session: seat,
        live={seat: object()},
        tasks={seat: reading},
        caught={seat: caught},
        outdated={},
        _listen=Mock(),
    )
    replay = asyncio.create_task(lifecycle.replay(svc, session))
    try:
        await asyncio.sleep(0)
        assert not replay.done(), "An existing reader has not necessarily caught up"
        caught.set()
        await asyncio.wait_for(replay, 0.5)
    finally:
        reading.cancel()
        replay.cancel()
        await asyncio.gather(reading, replay, return_exceptions=True)
