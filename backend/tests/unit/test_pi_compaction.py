"""A pi session compacting its context says so, and a turn it compacts to save
is not a turn that failed.

pi says it is compacting only on its live stream (``compaction_start`` /
``compaction_end``); the entry log has a ``compaction`` entry when it worked and
nothing when it started or failed. And a call that overflowed the context is
written as a failure pi reports it will not retry, before it decides to compact
and ask it again. Read from the entries alone, the room heard nothing while the
session compacted, and a turn pi rescued by compacting ended as a failure.

Each test replays a session recorded from pi 0.85.1 against a scripted provider
(``fixtures/pi-compaction-*.json``) through the real runner, over its socket,
and reads back what the platform reads: the runner's log, through pi's
translator and the rule that ends a turn.
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
from app.domain.agent.service import AgentCompacting, AgentMessage, AgentResult

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


async def settled(runner: Runner) -> None:
    for _ in range(200):
        if not (await call(runner.state, "ping"))["working"] and not runner.verdicts:
            return
        await asyncio.sleep(0.02)


async def replay(tmp_path: Path, recording: dict, turns: int) -> list[dict]:
    """Send ``turns`` inputs through the runner; answer the log it keeps."""
    fixture = tmp_path / "recording.json"
    fixture.write_text(json.dumps(recording))
    shim = tmp_path / "pi"
    shim.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "{fixture}" "$@"\n')
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
        for number in range(1, turns + 1):
            await call(
                runner.state,
                "send",
                {
                    "input_id": str(uuid.uuid4()),
                    "text": f"question {number}",
                    "work_id": f"work-{number}",
                },
            )
            await settled(runner)
        return (await call(runner.state, "entries"))["entries"]
    finally:
        await runner.close()


def recorded(name: str) -> dict:
    return json.loads((FIXTURES / f"pi-compaction-{name}.json").read_text())


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


def compactions(log: list[dict]) -> list[dict]:
    return [record for record in log if record["type"] == "cheese_compacting"]


@pytest.mark.anyio
async def test_a_compaction_after_a_turn_says_when_it_starts_and_ends(tmp_path):
    log = await replay(tmp_path, recorded("threshold"), turns=2)
    events, ends = read(log)

    assert [type(e) for e in events] == [
        AgentMessage,
        AgentResult,
        AgentMessage,
        AgentResult,
        AgentCompacting,
        AgentCompacting,
    ]
    assert [(e.done, e.error) for e in events[-2:]] == [(False, ""), (True, "")]
    # pi compacts once the second turn has answered, and on its behalf.
    assert {r["cheese"]["work_id"] for r in compactions(log)} == {"work-2"}
    assert len(ends) == 2


@pytest.mark.anyio
async def test_a_compaction_that_fails_says_why(tmp_path):
    log = await replay(tmp_path, recorded("failed"), turns=2)
    events, _ = read(log)

    compacting = [e for e in events if isinstance(e, AgentCompacting)]
    assert [e.done for e in compacting] == [False, True]
    assert "summary request refused" in compacting[1].error


@pytest.mark.anyio
async def test_a_call_that_overflowed_is_compacted_and_asked_again_not_failed(
    tmp_path,
):
    log = await replay(tmp_path, recorded("overflow"), turns=2)
    events, ends = read(log)

    second = events[2:]
    assert [type(e) for e in second] == [
        AgentCompacting,
        AgentCompacting,
        AgentMessage,
        AgentResult,
    ]
    result = second[-1]
    assert result.is_error is False
    assert result.text == "second answer, on the compacted context"
    # The second turn ends once, on that answer, and not on the overflow.
    assert len(ends) == 2 and ends[-1] == len(log) - 1
    assert not [r for r in log if r["type"] == "cheese_gave_up"]


@pytest.mark.anyio
async def test_a_compaction_still_running_when_the_next_input_arrives_keeps_its_turn(
    tmp_path,
):
    """pi compacts after the turn has answered, so the room may already have
    sent the next input when the compaction ends. The end belongs to the turn
    whose line said it started, or that line would say it is compacting for
    ever."""
    recording = recorded("threshold")
    stream = recording["stream"]
    start = next(
        index
        for index, step in enumerate(stream)
        if step.get("event", {}).get("type") == "compaction_start"
    )
    stream.insert(start + 1, {"pause": True})
    log = await replay(tmp_path, recording, turns=3)

    assert [(r["done"], r["cheese"]["work_id"]) for r in compactions(log)] == [
        (False, "work-2"),
        (True, "work-2"),
    ]
