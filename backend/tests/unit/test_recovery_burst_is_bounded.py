"""A restart reconnects the whole fleet at once; recovery must not follow it.

Every device runs `recover_business_state` when it attaches, and after a
backend restart every device attaches in the same instant — 71 on dev. Each
recovery walks that machine's sessions, taking a database connection per
session and then talking to the machine. Unbounded, the burst asks for more
connections than the pool has (20 + 15), and what it starves is every other
request: a restart came out as minutes of 「QueuePool limit … connection timed
out」 on page loads and background jobs (2026-09-19).
"""

import asyncio

import pytest

from app.api.routes import connector


class _Wakeup:
    async def wake_device(self, device_id: str) -> None:
        return None


@pytest.fixture
def quiet_reconnect(monkeypatch):
    monkeypatch.setattr("app.core.background.spawn", lambda coro, *, name: coro.close())
    monkeypatch.setattr("app.api.deps.get_cloud_wakeup", lambda: _Wakeup())


@pytest.mark.anyio
async def test_the_whole_fleet_attaching_recovers_a_few_at_a_time(
    monkeypatch, quiet_reconnect
) -> None:
    running = 0
    high_water = 0
    release = asyncio.Event()

    class Chat:
        async def recover_sessions(self, device_id: str) -> int:
            nonlocal running, high_water
            running += 1
            high_water = max(high_water, running)
            await release.wait()
            running -= 1
            return 0

    monkeypatch.setattr("app.api.deps.get_chat_service", lambda: Chat())
    fleet = [
        asyncio.create_task(connector.recover_business_state(f"machine-{i}"))
        for i in range(71)
    ]
    for _ in range(10):
        await asyncio.sleep(0)
    assert high_water == connector._RECOVERY_AT_ONCE
    release.set()
    await asyncio.wait_for(asyncio.gather(*fleet), 5)
    # Queued, never dropped: every machine is recovered.
    assert high_water == connector._RECOVERY_AT_ONCE


@pytest.mark.anyio
async def test_a_machine_whose_recovery_fails_frees_its_place(
    monkeypatch, quiet_reconnect
) -> None:
    """A failing recovery must release the slot, or one broken machine would
    take a quarter of the fleet's recovery capacity with it."""
    seen: list[str] = []

    class Chat:
        async def recover_sessions(self, device_id: str) -> int:
            seen.append(device_id)
            raise ValueError("bad row")

    monkeypatch.setattr("app.api.deps.get_chat_service", lambda: Chat())
    for i in range(8):
        await connector.recover_business_state(f"machine-{i}")
    assert len(seen) == 8
    assert connector._recovering._value == connector._RECOVERY_AT_ONCE
