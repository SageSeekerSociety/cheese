"""Central executor readiness without database-backed room setup."""

import asyncio
import logging
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.domain.agent import execution
from app.domain.agent.central_provider import CentralChannel

_real_sleep = asyncio.sleep
PROVIDER = "app.domain.agent.central_provider"


def owner_failure(status: int, body: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://owner/call_executor")
    return httpx.HTTPStatusError(
        "executor request failed",
        request=request,
        response=httpx.Response(status, request=request, text=body),
    )


class Clock:
    """A clock that only moves when the code under test decides to wait.

    Setup's slow path is measured in minutes; a test cannot spend them, and
    sleeping for real would make what the log says depend on how fast the
    machine running the test is.
    """

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


def run_on(monkeypatch, clock):
    monkeypatch.setattr(PROVIDER + ".time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(PROVIDER + ".asyncio.sleep", clock.sleep)


def timing_lines(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if "central_setup_timing" in record.getMessage()
    ]


def central_without_database() -> CentralChannel:
    central = object.__new__(CentralChannel)
    central._hub = SimpleNamespace()
    return central


@pytest.mark.anyio
async def test_executor_readiness_retries_transient_owner_http_failure(monkeypatch):
    central = central_without_database()
    unavailable = owner_failure(500)
    call = AsyncMock(side_effect=[unavailable, {"pid": 123}])
    monkeypatch.setattr(execution, "call", call)
    monkeypatch.setattr("app.domain.agent.central_provider.asyncio.sleep", AsyncMock())

    await central._wait_executor(
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), {"device_id": "executor"}, False
    )

    assert call.await_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 403, 404, 502])
async def test_executor_readiness_does_not_retry_other_http_failure(
    monkeypatch, status
):
    central = central_without_database()
    failure = owner_failure(status)
    call = AsyncMock(side_effect=failure)
    monkeypatch.setattr(execution, "call", call)

    with pytest.raises(httpx.HTTPStatusError):
        await central._wait_executor(
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), {"device_id": "executor"}, False
        )
    call.assert_awaited_once()


@pytest.mark.anyio
async def test_executor_readiness_timeout_preserves_owner_http_failure(monkeypatch):
    central = central_without_database()
    unavailable = owner_failure(500)
    call = AsyncMock(side_effect=unavailable)
    sleep = AsyncMock()
    monkeypatch.setattr(execution, "call", call)
    monkeypatch.setattr("app.domain.agent.central_provider.asyncio.sleep", sleep)
    monkeypatch.setattr(
        "app.domain.agent.central_provider.time",
        SimpleNamespace(monotonic=Mock(side_effect=[0, 31])),
    )

    with pytest.raises(httpx.HTTPStatusError) as raised:
        await central._wait_executor(
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), {"device_id": "executor"}, False
        )

    assert raised.value is unavailable
    call.assert_awaited_once()
    sleep.assert_not_awaited()


@pytest.mark.anyio
async def test_a_wait_on_the_environment_says_which_state_holds_it(monkeypatch, caplog):
    central = central_without_database()
    clock = Clock()
    topic = uuid.uuid4()
    states = ["building", "building", "ready"]

    async def status(*args, **kwargs):
        clock.now += 10  # asking the machine is itself a round trip
        return {"state": states.pop(0)}

    monkeypatch.setattr(PROVIDER + ".environment_status", status)
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={"pid": 123}))
    run_on(monkeypatch, clock)

    with caplog.at_level(logging.INFO, logger=PROVIDER):
        await central._wait_executor(
            uuid.uuid4(), topic, uuid.uuid4(), {"device_id": "executor"}, True
        )

    lines = timing_lines(caplog)
    assert any(
        "phase=executor_wait " in line
        and "waiting_on=environment state=building" in line
        and str(topic) in line
        and "attempts=1" in line
        and "waited_ms=10000" in line
        for line in lines
    ), lines
    assert any("phase=executor_wait_done" in line for line in lines), lines


@pytest.mark.anyio
async def test_a_wait_on_a_failing_ping_says_what_the_ping_answered(
    monkeypatch, caplog
):
    central = central_without_database()
    clock = Clock()
    monkeypatch.setattr(
        execution,
        "call",
        AsyncMock(side_effect=owner_failure(500, "executor socket is gone")),
    )
    run_on(monkeypatch, clock)

    with caplog.at_level(logging.INFO, logger=PROVIDER):
        with pytest.raises(httpx.HTTPStatusError):
            await central._wait_executor(
                uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), {"device_id": "x"}, False
            )

    lines = timing_lines(caplog)
    assert lines, "half a minute of waiting must not pass in silence"
    assert all(
        "waiting_on=executor_ping" in line and "executor socket is gone" in line
        for line in lines
    ), lines


@pytest.mark.anyio
async def test_a_wait_that_is_over_at_once_stays_quiet(monkeypatch, caplog):
    central = central_without_database()
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={"pid": 123}))

    with caplog.at_level(logging.INFO, logger=PROVIDER):
        await central._wait_executor(
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), {"device_id": "x"}, False
        )

    assert timing_lines(caplog) == []


@pytest.mark.anyio
async def test_walking_the_workspace_is_timed_on_its_own(monkeypatch, caplog):
    central = central_without_database()
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={"entries": {}}))
    topic = uuid.uuid4()

    with caplog.at_level(logging.INFO, logger=PROVIDER):
        tree = await central._context_tree(topic, {"device_id": "x"}, time.monotonic())

    lines = timing_lines(caplog)
    assert tree == {"entries": {}}
    assert len(lines) == 1, lines
    assert "phase=context_tree" in lines[0] and str(topic) in lines[0]
    assert "took_ms=" in lines[0] and "waiting_on" not in lines[0]


@pytest.mark.anyio
async def test_a_slow_walk_says_so_while_it_is_still_walking(monkeypatch, caplog):
    central = central_without_database()
    spoke = asyncio.Event()

    class Trip(logging.Handler):
        def emit(self, record):
            if "waiting_on=context_fs" in record.getMessage():
                spoke.set()

    async def walk(*args, **kwargs):
        # Only returns once the wait has been reported, so a report that came
        # after the walk finished would deadlock instead of passing.
        await spoke.wait()
        return {"entries": {}}

    async def no_delay(seconds):
        await _real_sleep(0)

    monkeypatch.setattr(execution, "call", walk)
    monkeypatch.setattr(PROVIDER + ".asyncio.sleep", no_delay)
    trip = Trip()
    logging.getLogger(PROVIDER).addHandler(trip)
    try:
        with caplog.at_level(logging.INFO, logger=PROVIDER):
            tree = await asyncio.wait_for(
                central._context_tree(
                    uuid.uuid4(), {"device_id": "x"}, time.monotonic()
                ),
                5,
            )
    finally:
        logging.getLogger(PROVIDER).removeHandler(trip)

    assert tree == {"entries": {}}
    lines = timing_lines(caplog)
    assert any(
        "phase=context_tree" in line and "waiting_on=context_fs" in line
        for line in lines
    ), lines
