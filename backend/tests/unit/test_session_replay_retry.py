"""A replay that fails once because its runner is still cold is retried, then lands.

The give-up deadline that once lived beside this loop was reverted (it could not
tell a slow tail from an absent machine); the retry itself is the behaviour that
stays, and this test is what notices if a retry stops leading to a replay.
"""

import asyncio
import uuid

import pytest

from app.domain.agent import recovery
from app.domain.agent.device_hub import DeviceCallError
from app.domain.agent.harness import SessionRef
from app.domain.agent.recovery import SessionRecovery


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
