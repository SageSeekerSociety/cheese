"""A stall of the event loop shorter than the second `core/loop_lag` dumps
for is kept with the stack that held the loop (`core/loop_stalls`)."""

import asyncio
import importlib
import time


def _stalls():
    return importlib.import_module("app.core.loop_stalls")


def hold_the_loop_for_a_while() -> None:
    end = time.monotonic() + 0.35
    while time.monotonic() < end:
        sum(range(1000))


def test_a_stall_is_kept_with_the_frame_that_held_the_loop():
    loop_stalls = _stalls()

    async def go() -> None:
        watcher = asyncio.create_task(loop_stalls.watch_loop_stalls(iterations=12))
        await asyncio.sleep(0.12)
        hold_the_loop_for_a_while()
        await watcher

    before = loop_stalls.stall_status()["count"]
    asyncio.run(go())
    status = loop_stalls.stall_status()
    assert status["count"] == before + 1
    stall = status["recent"][0]
    assert stall["ms"] >= 250
    assert any("hold_the_loop_for_a_while" in frame for frame in stall["stack"])


def test_a_loop_that_keeps_yielding_keeps_no_stall():
    loop_stalls = _stalls()

    async def go() -> None:
        watcher = asyncio.create_task(loop_stalls.watch_loop_stalls(iterations=8))
        for _ in range(40):
            await asyncio.sleep(0.01)
        await watcher

    before = loop_stalls.stall_status()["count"]
    asyncio.run(go())
    assert loop_stalls.stall_status()["count"] == before
