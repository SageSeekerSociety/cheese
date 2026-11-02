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


def test_a_watcher_off_the_main_thread_says_it_is_not_measuring():
    import threading

    loop_stalls = _stalls()
    seen = {}

    def off_main() -> None:
        asyncio.run(loop_stalls.watch_loop_stalls(iterations=2))
        seen["active"] = loop_stalls.stall_status()["active"]

    thread = threading.Thread(target=off_main)
    thread.start()
    thread.join()
    assert seen == {"active": False}


def test_a_stopped_watcher_leaves_no_alarm_behind():
    import signal

    loop_stalls = _stalls()

    async def go() -> None:
        watcher = asyncio.create_task(loop_stalls.watch_loop_stalls())
        await asyncio.sleep(0.06)
        hold_the_loop_for_a_while()
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) is signal.SIG_DFL
    assert loop_stalls.stall_status()["active"] is False


def test_a_stall_inside_a_greenlet_names_the_frames_that_switched_to_it(
    monkeypatch,
):
    import greenlet

    loop_stalls = _stalls()
    # This module's frames stand in for the backend's own.
    monkeypatch.setattr(loop_stalls, "_APP", __file__.rsplit("/", 1)[0] + "/")

    def rows_decoded_in_a_greenlet() -> None:
        greenlet.greenlet(hold_the_loop_for_a_while).switch()

    async def go() -> None:
        watcher = asyncio.create_task(loop_stalls.watch_loop_stalls(iterations=12))
        await asyncio.sleep(0.12)
        rows_decoded_in_a_greenlet()
        await watcher

    asyncio.run(go())
    stack = loop_stalls.stall_status()["recent"][0]["stack"]
    assert any("hold_the_loop_for_a_while" in frame for frame in stack)
    assert any("rows_decoded_in_a_greenlet" in frame for frame in stack)
