"""A replay that cannot read its session must stop holding the room open.

While a room's replay task exists, `sweep_orphans` skips the whole room, so a
session whose runner socket never comes back kept every turn there open for the
life of the process — never reconciled, never reported.
"""

import asyncio
import uuid

import pytest

from app.domain.agent import recovery
from app.domain.agent.device_hub import DeviceCallError
from app.domain.agent.harness import SessionRef
from app.domain.agent.recovery import SessionRecovery


class _Compute:
    def __init__(self):
        self.calls = 0

    async def replay(self, session):
        self.calls += 1
        raise DeviceCallError("no such file or directory")


class _Prewarm:
    def nudge(self, session):
        raise AssertionError("nothing was read, so nothing may be nudged")


@pytest.mark.anyio
async def test_a_session_that_never_replays_stops_blocking_the_room(
    monkeypatch, caplog
):
    monkeypatch.setattr(recovery, "REPLAY_RETRY_S", 0.001)
    monkeypatch.setattr(recovery, "REPLAY_GIVE_UP_S", 0.02)

    service = object.__new__(SessionRecovery)
    service._compute = _Compute()
    service._replay_slots = asyncio.Semaphore(1)
    service.prewarm = _Prewarm()
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "reviewer", harness="pi")

    # It returns (rather than retrying forever), and it did try more than once.
    await asyncio.wait_for(service._replay_room([session], after=None), timeout=1)
    assert service._compute.calls > 1
    assert "given up" in caplog.text


class _ColdRunner:
    """One failure (a runner still cold), then the tail reads."""

    def __init__(self):
        self.calls = 0

    async def replay(self, session):
        self.calls += 1
        if self.calls == 1:
            raise DeviceCallError("dial unix: no such file")


class _RecordingPrewarm:
    def __init__(self):
        self.nudged = []

    def nudge(self, session):
        self.nudged.append(session)


@pytest.mark.anyio
async def test_a_cold_runner_is_retried_and_then_replayed(monkeypatch):
    monkeypatch.setattr(recovery, "REPLAY_RETRY_S", 0.001)

    service = object.__new__(SessionRecovery)
    service._compute = _ColdRunner()
    service._replay_slots = asyncio.Semaphore(1)
    service.prewarm = _RecordingPrewarm()
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "reviewer", harness="pi")

    await asyncio.wait_for(service._replay_room([session], after=None), timeout=1)
    assert service._compute.calls == 2
    assert service.prewarm.nudged == [session]
