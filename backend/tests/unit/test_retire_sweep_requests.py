"""Every device attaching asks for an archived-room sweep, and when the
connection owner is released every device attaches at once — 72 on 2026-09-19,
each starting its own sweep. A sweep is global (every due operation, every
online machine's inventory), so the 72 asked the same question 72 times, and
each held a pooled connection per operation across its device calls: the
backend's pool (20 + 15) saturated for ten minutes and every other request
became a QueuePool alert. Asking again while one runs is answered by running it
once more when it finishes."""

import asyncio

import pytest

from app.domain.topic import retire


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(retire, "_sweeping", False)
    monkeypatch.setattr(retire, "_sweep_again", False)


async def test_requests_during_a_sweep_add_up_to_one_more_sweep(monkeypatch):
    started = 0
    release = asyncio.Event()

    async def sweep(_sessions):
        nonlocal started
        started += 1
        await release.wait()
        return {"completed": 0, "pending": 0}

    monkeypatch.setattr(retire, "sweep_retired_storage", sweep)
    retire.request_sweep(lambda: None, name="cleanup device reconnect")
    await asyncio.sleep(0)
    assert started == 1
    # The fleet re-attaching while that sweep is still asking machines around.
    for _ in range(72):
        retire.request_sweep(lambda: None, name="cleanup device reconnect")
    await asyncio.sleep(0)
    assert started == 1
    release.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert started == 2
    for _ in range(5):
        await asyncio.sleep(0)
    assert started == 2 and not retire._sweeping


async def test_a_request_after_a_finished_sweep_runs_a_new_one(monkeypatch):
    started = 0

    async def sweep(_sessions):
        nonlocal started
        started += 1
        return {"completed": 0, "pending": 0}

    monkeypatch.setattr(retire, "sweep_retired_storage", sweep)
    retire.request_sweep(lambda: None, name="archived-room cleanup")
    for _ in range(3):
        await asyncio.sleep(0)
    retire.request_sweep(lambda: None, name="archived-room cleanup")
    for _ in range(3):
        await asyncio.sleep(0)
    assert started == 2


async def test_a_sweep_that_raises_does_not_block_the_next_request(monkeypatch):
    calls = 0

    async def sweep(_sessions):
        nonlocal calls
        calls += 1
        raise RuntimeError("inventory failed")

    monkeypatch.setattr(retire, "sweep_retired_storage", sweep)
    retire.request_sweep(lambda: None, name="archived-room cleanup")
    for _ in range(3):
        await asyncio.sleep(0)
    assert calls == 1 and not retire._sweeping
    retire.request_sweep(lambda: None, name="archived-room cleanup")
    for _ in range(3):
        await asyncio.sleep(0)
    assert calls == 2
