"""The watchdog that says the event loop stalled, so the next alert is not Redis's.

Everything this process does shares one event loop, so a stretch of work that
never awaits stops every other request for as long as it runs. From outside it
looks like whatever timed out first — 220 `Timeout reading from
192.168.16.6:6379` in ten minutes on 2026-09-19, on a box at load 3 whose
Valkey answered its slowest command in 187 ms.
"""

import asyncio
import logging
import time

import pytest

from app.core import loop_lag


@pytest.fixture(autouse=True)
def fresh():
    """The reading is process-wide, so a test that moves it puts it back —
    otherwise the health check in another file reads this file's numbers."""
    loop_lag._worst = 0.0
    loop_lag._recent = 0.0
    yield
    loop_lag._worst = 0.0
    loop_lag._recent = 0.0


@pytest.mark.anyio
async def test_a_loop_nobody_blocks_reports_no_stall(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="cheesex.loop"):
        await loop_lag.watch_loop_lag(interval_s=0.01, stall_s=0.5, iterations=3)
    assert [r for r in caplog.records if r.name == "cheesex.loop"] == []
    assert loop_lag.lag_status()["worst_ms"] < 500


@pytest.mark.anyio
async def test_work_that_never_awaits_is_named_with_how_long_it_held(caplog) -> None:
    async def block_once() -> None:
        await asyncio.sleep(0.02)
        time.sleep(0.3)  # noqa: ASYNC251 — the thing being measured

    watching = asyncio.create_task(
        loop_lag.watch_loop_lag(interval_s=0.01, stall_s=0.1, iterations=40)
    )
    with caplog.at_level(logging.WARNING, logger="cheesex.loop"):
        await block_once()
        await watching
    stalls = [r for r in caplog.records if r.name == "cheesex.loop"]
    assert len(stalls) == 1, stalls
    assert stalls[0].levelname == "WARNING"
    assert loop_lag.lag_status()["worst_ms"] >= 200


@pytest.mark.anyio
async def test_a_loop_that_keeps_stalling_says_so_once_a_minute(caplog) -> None:
    """A process that is stalling repeatedly must not also flood the channel —
    the same rule the pool-saturation warning follows."""

    async def block() -> None:
        for _ in range(3):
            await asyncio.sleep(0.02)
            time.sleep(0.2)  # noqa: ASYNC251 — the thing being measured

    watching = asyncio.create_task(
        loop_lag.watch_loop_lag(interval_s=0.01, stall_s=0.1, iterations=80)
    )
    with caplog.at_level(logging.WARNING, logger="cheesex.loop"):
        await block()
        await watching
    assert len([r for r in caplog.records if r.name == "cheesex.loop"]) == 1


@pytest.mark.anyio
async def test_the_health_check_reports_the_lag_without_failing_readiness() -> None:
    from app.api.routes.health import _check_event_loop

    loop_lag._recent = 3.0
    loop_lag._worst = 4.0
    reading = _check_event_loop()
    assert reading == {"status": "stalling", "recent_ms": 3000.0, "worst_ms": 4000.0}
    # `event_loop` is not in the required set, so a stalling loop does not make
    # the process unready — it makes it visible.
    from app.api.routes.health import _REQUIRED_CHECKS

    assert "event_loop" not in _REQUIRED_CHECKS
