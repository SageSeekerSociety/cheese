"""A turn pi compacts to save is not a turn that failed, and a compaction's
line belongs to the turn it started in.

A call that overflowed the context is written as a failure pi reports it will
not retry (``agent_end``), and only then does pi compact and ask the call again.
Ending the turn at ``agent_end`` closed it as a failure while pi went on to
answer. And pi also compacts after a turn has answered, so the room may send
the next input before the compaction ends.

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

from app.domain.agent.harness.driven.runner import SessionStart
from app.domain.agent.harness.pi.events import Assembler
from app.domain.agent.harness.pi.runner import Runner, socket_path
from app.domain.agent.harness.pi.subscription import Subscription
from app.domain.agent.service import AgentCompacting, AgentResult
from tests.support.room_machine import NO_MACHINE

FAKE = Path(__file__).resolve().parents[1] / "support/fake_pi.py"
NONCES = {
    1: "⟪w:00000000000000000000c001⟫",
    2: "⟪w:00000000000000000000c002⟫",
    3: "⟪w:00000000000000000000c003⟫",
}
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
        SessionStart("system prompt", None, agent_handle="teammate"),
        binary=str(shim),
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin"},
        args=[],
        target=NO_MACHINE,
    )
    try:
        for number in range(1, turns + 1):
            await call(
                runner.state,
                "send",
                {
                    "input_id": str(uuid.uuid4()),
                    "text": f"question {number} {NONCES[number]}",
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
    assembler = Assembler("session-1", harness="pi")
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
async def test_a_call_that_overflowed_is_compacted_and_asked_again_not_failed(
    tmp_path,
):
    log = await replay(tmp_path, recorded("overflow"), turns=2)
    events, ends = read(log)

    # Turn 1's events now include the turn-opening AgentUserEntry (FB-56),
    # so the second turn starts one slot later.
    second = events[3:]
    compacting = [e for e in second if isinstance(e, AgentCompacting)]
    assert [(e.done, e.error) for e in compacting] == [(False, ""), (True, "")]
    results = [e for e in second if isinstance(e, AgentResult)]
    assert len(results) == 1
    assert results[0].is_error is False
    assert results[0].text == "second answer, on the compacted context"
    # The second turn ends once, on that answer, and not on the overflow.
    assert len(ends) == 2
    assert (log[ends[-1]].get("message") or {}).get("stopReason") == "stop"
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


@pytest.mark.anyio
async def test_a_compaction_stays_with_the_work_it_started_under_across_a_new_input(
    tmp_path,
):
    """start imported under S, a live end, then a THIRD nonce's user entry
    consumed in between (owner verifiably moves S→T): the end still lands
    with S — one compaction, one work, across a new input (FB-56 P2-3)."""
    recording = {
        "why": "compaction pair split by a new input's user entry",
        "stream": [
            {"entry": _msg("u1", "user", f"question 1 {NONCES[1]}")},
            {"entry": _msg("a1", "assistant", "answer 1", parent="u1")},
            {"event": {"type": "compaction_start"}},
            {"pause": True},
            {"entry": _msg("u2", "user", f"question 2 {NONCES[2]}", parent="a1")},
            {"entry": _msg("a2", "assistant", "answer 2", parent="u2")},
            {"event": {"type": "compaction_end"}},
            {"pause": True},
        ],
    }
    log = await replay(tmp_path, recording, turns=2)

    assert [(r["done"], r["cheese"]["work_id"]) for r in compactions(log)] == [
        (False, "work-1"),
        (True, "work-1"),
    ], "同一 compaction 同源：start/end 都归 S，不被中间的新输入改归 T"
    stamps = {
        record["id"]: (record.get("cheese") or {}).get("work_id")
        for record in log
        if record.get("type") == "message"
    }
    assert stamps["u2"] == "work-2" and stamps["a2"] == "work-2", (
        "第三 nonce 的 T 确实被消费（owner 已变 T）"
    )


def _msg(entry_id, role, text, parent=None):
    return {
        "type": "message",
        "id": entry_id,
        "parentId": parent,
        "timestamp": "2026-10-01T00:00:00.000Z",
        "message": {
            "role": role,
            "content": [{"type": "text", "text": text}],
            **({"stopReason": "stop"} if role == "assistant" else {}),
        },
    }
