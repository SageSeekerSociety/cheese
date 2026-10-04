"""A session's mirror on the backend forgets what nobody will read again, and
gives the disk back.

The mirror is a sqlite file per seat that lives as long as the room does. What
it keeps past a day is only what is still going on.
"""

import asyncio
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.claude_code.backlog import (
    TASKS_KEPT,
    Known,
    control_state,
)
from app.domain.agent.harness.claude_code.subscription import Subscription
from app.domain.agent.harness.codex.backlog import CodexBacklog
from app.domain.agent.harness.codex.journal import Journal as CodexJournal
from app.domain.agent.service import AgentToolResult

DAY_S = 24 * 3600


def _on_disk(path: Path) -> int:
    wal = path.with_name(path.name + "-wal")
    return path.stat().st_size + (wal.stat().st_size if wal.exists() else 0)


def test_a_mirror_that_forgot_a_busy_day_gives_its_disk_back(tmp_path):
    path = tmp_path / "events.sqlite"
    journal = CodexJournal(path)
    old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    now = datetime.now(UTC).isoformat()
    delta = {"method": "item/agentMessage/delta", "params": {"delta": "x" * 1000}}
    journal.import_events(
        [{"sequence": n, "at": old, "record": delta} for n in range(1, 3001)]
    )
    journal.acknowledge(3000)
    journal.import_events(
        [{"sequence": n, "at": now, "record": delta} for n in range(3001, 3011)]
    )
    journal.close()
    busy = _on_disk(path)

    CodexBacklog(path).forget(older_than_s=DAY_S)

    connection = sqlite3.connect(path)
    try:
        kept = [row[0] for row in connection.execute("SELECT sequence FROM events")]
        (free,) = connection.execute("PRAGMA freelist_count").fetchone()
    finally:
        connection.close()
    assert kept == list(range(3001, 3011))
    assert free == 0
    assert _on_disk(path) < busy / 10


def _at(sequence: int, record: dict) -> dict:
    return {"sequence": sequence, "at": datetime.now(UTC).isoformat(), "record": record}


def _calls(first: int, work: str, calls: list[tuple[str, str]]) -> list[dict]:
    """The session calling ``calls`` (id, tool) one after the other."""
    return [
        _at(
            first + n,
            {
                "type": "assistant",
                "uuid": f"call-{call}",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": call,
                            "name": tool,
                            "input": {"description": f"{tool} {call}", "prompt": "go"},
                        }
                    ],
                },
                "cheese": {"work_id": work},
            },
        )
        for n, (call, tool) in enumerate(calls)
    ]


def _returned(sequence: int, work: str, call: str, text: str) -> dict:
    return _at(
        sequence,
        {
            "type": "user",
            "uuid": f"returned-{call}",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": call, "content": text}
                ],
            },
            "cheese": {"work_id": work},
        },
    )


def _task(sequence: int, work: str, task: str, call: str, status: str) -> dict:
    record = {
        "type": "system",
        "subtype": "task_started" if status == "running" else "task_notification",
        "task_id": task,
        "tool_use_id": call,
        "task_type": "local_agent",
        "description": f"task {task}",
        "cheese": {"work_id": work},
    }
    if status != "running":
        record["status"] = status
    return _at(sequence, record)


def _mirror(tmp_path, journal: list[dict], landed: list[object]) -> Subscription:
    async def call(method: str, params: dict) -> dict:
        return {"events": [e for e in journal if e["sequence"] > params["after"]]}

    async def consume(project, topic, work_id, event, eid, seen, unsolicited):
        landed.append(event)

    async def nothing(*_):
        pass

    return Subscription(
        SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese", harness="claude-code"),
        tmp_path / "records.sqlite",
        call,
        consume,
        nothing,
        session_id=None,
        recipient_handle="cheese",
        announce=nothing,
    )


async def _forget_everything_learned_so_far(reading: Subscription) -> None:
    """Retention, as if everything learned so far were older than it keeps."""
    await asyncio.sleep(0.01)
    await reading.on_disk(reading.reader().forget, older_than_s=0)


@pytest.mark.anyio
async def test_old_facts_of_finished_calls_go_and_a_running_subagents_stay(tmp_path):
    work = str(uuid.uuid4())
    journal = [
        *_calls(1, work, [("toolu_bash", "Bash"), ("toolu_agent", "Agent")]),
        _returned(3, work, "toolu_bash", "ok"),
        _task(4, work, "task-1", "toolu_agent", "running"),
    ]
    landed: list[object] = []
    reading = _mirror(tmp_path, journal, landed)
    try:
        await reading.drain()
        await _forget_everything_learned_so_far(reading)

        # What a backend starting now would know, and what this one holds.
        held = reading.known
        assert held is not None
        for facts in (Known.read(reading.path).facts, held.facts):
            assert "call:toolu_bash" not in facts
            assert {"call:toolu_agent", "task:task-1"} <= facts.keys()

        # The subagent comes back after retention, and still reports as itself.
        journal.append(_returned(5, work, "toolu_agent", "The cache key changed."))
        await reading.drain()
        reports = [e for e in landed if isinstance(e, AgentToolResult)]
        assert [(r.name, r.description) for r in reports] == [
            ("Agent", "Agent toolu_agent")
        ]
    finally:
        await reading.release()


@pytest.mark.anyio
async def test_facts_from_before_the_mirror_kept_their_age_are_not_taken_as_old(
    tmp_path,
):
    work = str(uuid.uuid4())
    journal = _calls(1, work, [("toolu_agent", "Agent")])
    landed: list[object] = []
    reading = _mirror(tmp_path, journal, landed)
    try:
        await reading.drain()
        # A mirror written before this deploy has its facts and no ages.
        connection = sqlite3.connect(reading.path)
        with connection:
            connection.execute("DROP TABLE learned")
        connection.close()
        await _forget_everything_learned_so_far(reading)

        journal.append(_returned(2, work, "toolu_agent", "Done."))
        await reading.drain()
        assert [e.name for e in landed if isinstance(e, AgentToolResult)] == ["Agent"]
    finally:
        await reading.release()


@pytest.mark.anyio
async def test_finished_tasks_the_controls_no_longer_list_are_forgotten(tmp_path):
    work = str(uuid.uuid4())
    finished = TASKS_KEPT + 20
    journal = [
        _task(n + 1, work, f"task-{n}", f"toolu_{n}", "completed")
        for n in range(finished)
    ]
    journal.append(_task(finished + 1, work, "task-live", "toolu_live", "running"))
    reading = _mirror(tmp_path, journal, [])
    try:
        await reading.drain()
        shown = control_state(reading.path)["tasks"]
        await reading.on_disk(reading.reader().forget, older_than_s=DAY_S)

        assert control_state(reading.path)["tasks"] == shown
        connection = sqlite3.connect(reading.path)
        try:
            (kept,) = connection.execute(
                "SELECT COUNT(*) FROM state WHERE key LIKE 'control:task:%'"
            ).fetchone()
        finally:
            connection.close()
        assert kept == TASKS_KEPT + 1
    finally:
        await reading.release()
