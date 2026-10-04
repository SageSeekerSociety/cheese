"""Building a session's executor does not stop the backend answering anyone else.

Every tool call a session makes asks for its work lease, and the lease brings
the executor up: it prepares a running one in place or installs a new one, from
a payload of about a megabyte read and encoded each time. Built on the event
loop, that held every other request for as long as it took — on dev on
2026-10-04 an idle `/healthz` answered at p90 235 ms, at worst 3.5 s.
"""

import asyncio
import json
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.agent import execution
from app.domain.agent.harness.claude_code import executor_launch
from app.domain.machine import session_work
from tests.executor_release import running

pytestmark = pytest.mark.anyio

#: How long building the payload takes here. A loop that waits this long for
#: it has been held; one that does not was free for everybody else.
BUILD_S = 0.3

INFO = {"state": "/executor/state", "workspace": "/executor/work", "mcp_servers": []}


def _slow(result):
    def build(*_args, **_kwargs):
        time.sleep(BUILD_S)
        return result

    return build


async def _worst_stall_during(work):
    """Run ``work`` and say the longest the loop went without a turn meanwhile."""
    worst = 0.0
    finished = asyncio.Event()

    async def tick():
        nonlocal worst
        while not finished.is_set():
            before = time.perf_counter()
            await asyncio.sleep(0.01)
            worst = max(worst, time.perf_counter() - before - 0.01)

    ticking = asyncio.create_task(tick())
    await asyncio.sleep(0)
    try:
        result = await work
    finally:
        finished.set()
        await ticking
    return result, worst


async def _start(hub, lease):
    return await session_work._start_executor(
        hub,
        lease,
        device_id="machine",
        project_id=uuid.uuid4(),
        work_resource=str(uuid.uuid4()),
        setup={},
        sandbox=False,
    )


async def test_installing_an_executor_leaves_the_loop_free(monkeypatch):
    monkeypatch.setattr(executor_launch, "script", _slow("print('installed')"))
    hub = SimpleNamespace(
        exec=AsyncMock(return_value={"exit": 0, "stdout": json.dumps(INFO)})
    )

    started, worst = await _worst_stall_during(_start(hub, None))

    assert started == INFO
    assert hub.exec.await_args.kwargs["stdin"] == "print('installed')"
    assert worst < BUILD_S / 2


async def test_preparing_a_running_executor_leaves_the_loop_free(monkeypatch):
    monkeypatch.setattr(executor_launch, "payload_for", _slow({"prepared": True}))
    sent = {}

    async def call(_lease, method, params, **_kwargs):
        if method == "ping":
            return running()
        sent[method] = params
        return INFO

    monkeypatch.setattr(execution, "call", call)
    hub = SimpleNamespace(exec=AsyncMock())

    started, worst = await _worst_stall_during(_start(hub, {"state": "/executor"}))

    assert started == INFO
    assert sent == {"prepare": {"prepared": True}}
    hub.exec.assert_not_awaited()
    assert worst < BUILD_S / 2
