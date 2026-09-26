"""A model call pi retries is not the end of the turn.

pi writes a failed call into its entry log as an assistant entry that stopped on
``error`` — and only then says, on its live stream, whether it will try again.
Read from the entries alone, the first failure looked like the turn ending: the
room closed the turn on an empty "reply" while pi went on retrying.

Each test replays a turn recorded from pi 0.85.1 (``fixtures/pi-retry-*.json``)
through the real runner, over its socket, and reads back what the platform
reads: the runner's log, through pi's translator and the rule that ends a turn.
"""

import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest

from app.domain.agent.harness import Opening
from app.domain.agent.harness.pi.events import Assembler
from app.domain.agent.harness.pi.runner import Runner, socket_path
from app.domain.agent.harness.pi.subscription import Subscription
from app.domain.agent.service import AgentMessage, AgentResult, AgentRetrying

FAKE = Path(__file__).resolve().parents[1] / "support/fake_pi.py"
FIXTURES = Path(__file__).parent / "fixtures"


async def call(state: Path, method: str, params: dict | None = None) -> dict:
    reader, writer = await asyncio.open_unix_connection(socket_path(state))
    try:
        writer.write(
            json.dumps({"method": method, "params": params or {}}).encode() + b"\n"
        )
        await writer.drain()
        answer = json.loads(await reader.readline())
    finally:
        writer.close()
        await writer.wait_closed()
    if "error" in answer:
        raise RuntimeError(answer["error"])
    return answer["result"]


async def replay(tmp_path: Path, recording: str) -> list[dict]:
    """Run one recorded turn through the runner; answer the log it keeps."""
    shim = tmp_path / "pi"
    shim.write_text(
        f"#!/bin/sh\nexec {sys.executable} {FAKE} "
        f'{FIXTURES / f"pi-retry-{recording}.json"} "$@"\n'
    )
    shim.chmod(0o700)
    runner = Runner(tmp_path / "state")
    await runner.start(
        Opening("system prompt", None, agent_handle="teammate"),
        binary=str(shim),
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin"},
        args=[],
    )
    try:
        await call(
            runner.state,
            "send",
            {"input_id": str(uuid.uuid4()), "text": "开始", "work_id": "work-1"},
        )
        # Until pi has settled and the runner has written down what it heard.
        for _ in range(200):
            log = (await call(runner.state, "entries"))["entries"]
            if not (await call(runner.state, "ping"))["working"] and not (
                runner.verdicts
            ):
                return (await call(runner.state, "entries"))["entries"]
            await asyncio.sleep(0.02)
        return log
    finally:
        await runner.close()


def read(log: list[dict]) -> tuple[list, list[int]]:
    """The events the room gets, and the positions in the log that end a turn."""
    assembler = Assembler("session-1")
    events = [event for record in log for event in assembler.accept(record)]
    ends = [
        index
        for index, record in enumerate(log)
        if Subscription.ends_turn(None, record, None)  # type: ignore[arg-type]
    ]
    return events, ends


@pytest.mark.anyio
async def test_a_turn_whose_request_is_retried_ends_once_with_the_answer(tmp_path):
    log = await replay(tmp_path, "recovered")
    events, ends = read(log)

    assert [type(e) for e in events] == [
        AgentRetrying,
        AgentRetrying,
        AgentRetrying,
        AgentMessage,
        AgentResult,
    ]
    assert [(e.attempt, e.max_attempts) for e in events[:3]] == [(1, 3), (2, 3), (3, 3)]
    assert "503" in events[0].error
    assert events[-1].is_error is False
    assert events[-1].text == "done after retry"
    # One ending, and it is the answer — none of the three failed calls.
    assert ends == [len(log) - 1]


@pytest.mark.anyio
async def test_each_retry_is_written_behind_the_failure_it_retries(tmp_path):
    log = await replay(tmp_path, "recovered")

    failures = [
        i
        for i, record in enumerate(log)
        if (record.get("message") or {}).get("stopReason") == "error"
    ]
    retries = [i for i, record in enumerate(log) if record["type"] == "cheese_retrying"]
    assert len(failures) == len(retries) == 3
    # Each one right behind the failed call it is about, not wherever the
    # runner happened to pull it in.
    assert retries == [f + 1 for f in failures]
    # The runner's own records belong to the same work as pi's entries.
    assert {record["cheese"]["work_id"] for record in log[2:]} == {"work-1"}


@pytest.mark.anyio
async def test_retries_that_run_out_end_the_turn_as_a_failure(tmp_path):
    log = await replay(tmp_path, "exhausted")
    events, ends = read(log)

    assert [type(e) for e in events] == [AgentRetrying] * 3 + [AgentResult]
    result = events[-1]
    assert result.is_error is True
    assert result.api_error_status == 503
    assert "503" in result.text
    assert ends == [len(log) - 1]
    assert log[-1]["type"] == "cheese_gave_up"


@pytest.mark.anyio
async def test_a_request_pi_does_not_retry_ends_the_turn_at_once(tmp_path):
    log = await replay(tmp_path, "refused")
    events, ends = read(log)

    assert [type(e) for e in events] == [AgentResult]
    assert events[0].is_error is True
    assert events[0].api_error_status == 400
    assert ends == [len(log) - 1]
