"""The watchdog that says the event loop stalled, so the next alert is not Redis's.

Everything this process does shares one event loop, so a stretch of work that
never awaits stops every other request for as long as it runs. From outside it
looks like whatever timed out first — 220 `Timeout reading from
192.168.16.6:6379` in ten minutes on 2026-09-19, on a box at load 3 whose
Valkey answered its slowest command in 187 ms.
"""

import asyncio
import io
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.core import loop_lag


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Keep this process's reading and timer isolated from other tests."""
    loop_lag._worst = 0.0
    loop_lag._recent = 0.0
    monkeypatch.setattr(loop_lag, "_last_dump_at", None, raising=False)
    monkeypatch.setattr(loop_lag, "DUMP_FILE", io.StringIO(), raising=False)
    yield
    loop_lag._worst = 0.0
    loop_lag._recent = 0.0
    loop_lag._last_dump_at = None


def _stall_count(caplog, capsys) -> int:
    logged = sum(
        record.name == "cheesex.loop" and "event loop stalled" in str(record.msg)
        for record in caplog.records
    )
    captured = capsys.readouterr()
    return logged + (captured.out + captured.err).count("event loop stalled")


@pytest.mark.anyio
async def test_a_loop_nobody_blocks_reports_no_stall(capsys, caplog) -> None:
    await loop_lag.watch_loop_lag(interval_s=0.01, stall_s=0.5, iterations=3)
    assert _stall_count(caplog, capsys) == 0
    assert loop_lag.lag_status()["worst_ms"] < 500


@pytest.mark.anyio
async def test_work_that_never_awaits_is_named_with_how_long_it_held(
    monkeypatch,
) -> None:
    events: list[tuple[str, str]] = []

    class CapturingLogger:
        def warning(self, event: str, **_fields: object) -> None:
            events.append(("warning", event))

        def info(self, event: str, **_fields: object) -> None:
            events.append(("info", event))

    monkeypatch.setattr(loop_lag, "logger", CapturingLogger())

    async def block_once() -> None:
        await asyncio.sleep(0.02)
        time.sleep(0.3)  # noqa: ASYNC251 — the thing being measured

    watching = asyncio.create_task(
        loop_lag.watch_loop_lag(interval_s=0.01, stall_s=0.1, iterations=40)
    )
    await block_once()
    await watching
    assert events == [("warning", "event loop stalled")]
    assert loop_lag.lag_status()["worst_ms"] >= 200


@pytest.mark.anyio
async def test_a_blocked_loop_dumps_its_stack_to_the_configured_file(tmp_path) -> None:
    dump_file = tmp_path / "loop-lag.dump"
    script = """
import asyncio
import sys
import time
from app.core import loop_lag

loop_lag.DUMP_FILE = open(sys.argv[1], "w")

def unique_loop_lag_blocker_7e5d5c():
    time.sleep(1.5)

async def run():
    watching = asyncio.create_task(loop_lag.watch_loop_lag(iterations=1))
    await asyncio.sleep(0.05)
    unique_loop_lag_blocker_7e5d5c()
    await watching

asyncio.run(run())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(dump_file)],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "unique_loop_lag_blocker_7e5d5c" in dump_file.read_text()


@pytest.mark.anyio
async def test_a_loop_that_does_not_stall_does_not_dump(tmp_path) -> None:
    dump_file = tmp_path / "loop-lag.dump"
    script = """
import asyncio
import sys
from app.core import loop_lag

loop_lag.DUMP_FILE = open(sys.argv[1], "w")
asyncio.run(loop_lag.watch_loop_lag(interval_s=0.01, stall_s=0.1, iterations=3))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(dump_file)],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert dump_file.read_text() == ""


@pytest.mark.anyio
async def test_a_loop_that_keeps_stalling_dumps_and_warns_once_a_minute(
    capsys, caplog, monkeypatch, tmp_path
) -> None:
    """A process that is stalling repeatedly must not dump or warn on every loop."""
    dump_calls = 0
    cancel_calls = 0
    fired_count = 0
    armed_at: float | None = None
    dump_file = tmp_path / "loop-lag.dump"

    def dump_traceback_later(timeout: float, *, file) -> None:
        nonlocal armed_at, dump_calls
        assert timeout == 0.1
        assert file is dump_file
        armed_at = time.monotonic()
        dump_calls += 1

    def cancel_dump_traceback_later() -> None:
        nonlocal armed_at, cancel_calls, fired_count
        cancel_calls += 1
        if armed_at is not None and time.monotonic() - armed_at >= 0.1:
            fired_count += 1
        armed_at = None

    monkeypatch.setattr(loop_lag, "DUMP_FILE", dump_file)
    monkeypatch.setattr(
        loop_lag.faulthandler, "dump_traceback_later", dump_traceback_later
    )
    monkeypatch.setattr(
        loop_lag.faulthandler,
        "cancel_dump_traceback_later",
        cancel_dump_traceback_later,
    )

    async def block() -> None:
        for _ in range(3):
            await asyncio.sleep(0.02)
            time.sleep(0.2)  # noqa: ASYNC251 — the thing being measured

    watching = asyncio.create_task(
        loop_lag.watch_loop_lag(interval_s=0.01, stall_s=0.1, iterations=80)
    )
    await block()
    await watching
    assert _stall_count(caplog, capsys) == 1
    assert fired_count == 1
    assert cancel_calls == dump_calls


@pytest.mark.anyio
async def test_a_dump_below_the_lag_threshold_consumes_the_rate_limit(
    capsys, caplog, monkeypatch, tmp_path
) -> None:
    dump_calls = 0
    cancel_calls = 0
    fired_count = 0
    armed_at: float | None = None
    first_cancelled = asyncio.Event()
    real_monotonic = time.monotonic
    dump_file = tmp_path / "loop-lag.dump"

    def dump_traceback_later(timeout: float, *, file) -> None:
        nonlocal armed_at, dump_calls
        assert timeout == 0.1
        assert file is dump_file
        armed_at = real_monotonic()
        dump_calls += 1

    def cancel_dump_traceback_later() -> None:
        nonlocal armed_at, cancel_calls, fired_count
        cancel_calls += 1
        if armed_at is not None and real_monotonic() - armed_at >= 0.1:
            fired_count += 1
        armed_at = None
        if cancel_calls == 1:
            first_cancelled.set()

    monkeypatch.setattr(loop_lag, "DUMP_FILE", dump_file)
    monkeypatch.setattr(
        loop_lag.faulthandler, "dump_traceback_later", dump_traceback_later
    )
    monkeypatch.setattr(
        loop_lag.faulthandler,
        "cancel_dump_traceback_later",
        cancel_dump_traceback_later,
    )

    watching = asyncio.create_task(
        loop_lag.watch_loop_lag(
            interval_s=0.5, stall_s=0.1, report_every_s=60.0, iterations=2
        )
    )
    await asyncio.sleep(0.02)
    time.sleep(0.2)  # noqa: ASYNC251 — the thing being measured
    await first_cancelled.wait()
    time.sleep(0.2)  # noqa: ASYNC251 — the second short stall
    await watching

    assert _stall_count(caplog, capsys) == 0
    assert loop_lag.lag_status()["worst_ms"] < 100
    assert dump_calls == 1
    assert fired_count == 1
    assert cancel_calls == 1
    assert loop_lag._last_dump_at is not None


@pytest.mark.anyio
async def test_dump_is_armed_again_after_the_report_window(
    monkeypatch, tmp_path
) -> None:
    dump_calls = 0
    fired_count = 0
    armed_at: float | None = None
    real_monotonic = time.monotonic
    loop_lag._last_dump_at = real_monotonic() - 60.1
    dump_file = tmp_path / "loop-lag.dump"

    def dump_traceback_later(timeout: float, *, file) -> None:
        nonlocal armed_at, dump_calls
        assert timeout == 0.1
        assert file is dump_file
        armed_at = real_monotonic()
        dump_calls += 1

    def cancel_dump_traceback_later() -> None:
        nonlocal armed_at, fired_count
        if armed_at is not None and real_monotonic() - armed_at >= 0.1:
            fired_count += 1
        armed_at = None

    monkeypatch.setattr(loop_lag, "DUMP_FILE", dump_file)
    monkeypatch.setattr(
        loop_lag.faulthandler, "dump_traceback_later", dump_traceback_later
    )
    monkeypatch.setattr(
        loop_lag.faulthandler,
        "cancel_dump_traceback_later",
        cancel_dump_traceback_later,
    )

    watching = asyncio.create_task(
        loop_lag.watch_loop_lag(
            interval_s=0.5, stall_s=0.1, report_every_s=60.0, iterations=1
        )
    )
    await asyncio.sleep(0.02)
    time.sleep(0.2)  # noqa: ASYNC251 — the thing being measured
    await watching

    assert dump_calls == 1
    assert fired_count == 1
    assert loop_lag._last_dump_at is not None


@pytest.mark.anyio
async def test_the_health_check_reports_the_lag_without_failing_readiness() -> None:
    from app.core.health import _check_event_loop

    loop_lag._recent = 3.0
    loop_lag._worst = 4.0
    reading = _check_event_loop()
    assert reading == {"status": "stalling", "recent_ms": 3000.0, "worst_ms": 4000.0}
    # `event_loop` is not in the required set, so a stalling loop does not make
    # the process unready — it makes it visible.
    from app.core.health import _REQUIRED_CHECKS

    assert "event_loop" not in _REQUIRED_CHECKS
