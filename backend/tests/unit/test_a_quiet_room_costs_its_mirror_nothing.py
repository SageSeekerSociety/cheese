"""A room's mirror is read for what is new, not re-read on every pass.

Each pass over a Claude Code session asks its runner for anything new, ten
times a second while a turn is open. What the mirror already knows (which call
was which, which card a subagent works on) is needed only when something new
arrives, and it only grows; a pass with nothing new must not pay for it.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.claude_code import journal as cc_journal
from app.domain.agent.harness.claude_code.backlog import control_state
from app.domain.agent.harness.claude_code.subscription import Subscription
from app.domain.agent.service import AgentStepOutput, AgentToolResult


def _record(sequence: int, record: dict) -> dict:
    return {
        "sequence": sequence,
        "at": datetime.now(UTC).isoformat(),
        "record": record,
    }


def _opening(work: str) -> list[dict]:
    """A turn that starts a subagent and is still waiting on it."""
    stamp = {"work_id": work}
    return [
        _record(
            1,
            {
                "type": "user",
                "uuid": "echo",
                "message": {"role": "user", "content": "look into the build"},
                "cheese": {**stamp, "turn_start": True, "receipt": True},
            },
        ),
        _record(
            2,
            {
                "type": "assistant",
                "uuid": "spawn",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_agent",
                            "name": "Agent",
                            "input": {
                                "description": "Read the CI logs",
                                "prompt": "Find why the build fails.",
                            },
                        }
                    ],
                },
                "cheese": stamp,
            },
        ),
        _record(
            3,
            {
                "type": "system",
                "subtype": "task_started",
                "task_id": "task-1",
                "description": "Read the CI logs",
                "cheese": stamp,
            },
        ),
    ]


def _report(work: str) -> list[dict]:
    """The subagent comes back, a while later."""
    stamp = {"work_id": work}
    return [
        _record(
            4,
            {
                "type": "user",
                "uuid": "report",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_agent",
                            "content": "The cache key changed.",
                        }
                    ],
                },
                "cheese": stamp,
            },
        )
    ]


def _subscription(tmp_path, journal: list[dict], landed: list[object], asked: list):
    async def call(method: str, params: dict) -> dict:
        assert method == "events"
        asked.append(params["after"])
        return {"events": [e for e in journal if e["sequence"] > params["after"]]}

    async def consume(project, topic, work_id, event, eid, seen, unsolicited):
        landed.append(event)

    async def activity(project, seat, work_id, active):
        pass

    async def announce():
        pass

    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness="claude-code")
    return Subscription(
        session,
        tmp_path / "records.sqlite",
        call,
        consume,
        activity,
        session_id=None,
        recipient_handle=session.agent_handle,
        announce=announce,
    )


def _count_fact_reads(monkeypatch) -> list[str]:
    reads: list[str] = []
    original = cc_journal.Journal.facts

    def facts(self, prefix):
        reads.append(prefix)
        return original(self, prefix)

    monkeypatch.setattr(cc_journal.Journal, "facts", facts)
    return reads


@pytest.mark.anyio
async def test_a_pass_with_nothing_new_does_not_read_what_the_mirror_knows(
    tmp_path, monkeypatch
):
    work = str(uuid.uuid4())
    journal = _opening(work)
    landed: list[object] = []
    reading = _subscription(tmp_path, journal, landed, [])
    try:
        await reading.drain()
        reads = _count_fact_reads(monkeypatch)
        for _ in range(20):
            await reading.drain()
        assert reads == []
    finally:
        await reading.release()


@pytest.mark.anyio
async def test_a_subagent_reporting_passes_later_still_reports_as_itself(tmp_path):
    work = str(uuid.uuid4())
    journal = _opening(work)
    landed: list[object] = []
    asked: list[int] = []
    reading = _subscription(tmp_path, journal, landed, asked)
    try:
        await reading.drain()
        for _ in range(5):
            await reading.drain()
        journal.extend(_report(work))
        await reading.drain()
    finally:
        await reading.release()

    reports = [e for e in landed if isinstance(e, AgentToolResult)]
    assert [(r.name, r.description, r.text) for r in reports] == [
        ("Agent", "Read the CI logs", "The cache key changed.")
    ]
    assert not any(isinstance(e, AgentStepOutput) for e in landed)
    # Asked from where it got to, not from the start, every time.
    assert asked[-1] == 3


@pytest.mark.anyio
async def test_what_the_controls_show_is_what_the_passes_learned(tmp_path):
    work = str(uuid.uuid4())
    journal = _opening(work)
    reading = _subscription(tmp_path, journal, [], [])
    try:
        await reading.drain()
        await reading.drain()
    finally:
        await reading.release()

    tasks = control_state(tmp_path / "records.sqlite")["tasks"]
    assert tasks["task-1"]["status"] == "running"

    # A backend that comes up later starts from the mirror and goes on.
    journal.append(
        _record(
            5,
            {
                "type": "system",
                "subtype": "task_notification",
                "task_id": "task-1",
                "status": "completed",
                "cheese": {"work_id": work},
            },
        )
    )
    again = _subscription(tmp_path, journal, [], [])
    try:
        await again.drain()
    finally:
        await again.release()
    tasks = control_state(tmp_path / "records.sqlite")["tasks"]
    assert tasks["task-1"]["status"] == "completed"
    assert tasks["task-1"]["description"] == "Read the CI logs"
