"""The raw record gets every row a session's journal holds, and never at the
room's expense.

Rules a person can state without the code: what the session wrote reaches the
room even while the raw record cannot take it; the rows the raw record could
not take are handed to it again and are not lost to the mirror's retention in
the meantime; and a session the platform reads hands its rows to the raw
record at all.
"""

import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.agent.harness import CODEX, SessionRef
from app.domain.agent.harness.codex import journal as codex_journal
from app.domain.agent.harness.codex import subscription as codex
from app.domain.agent.harness.codex.backlog import CodexBacklog
from app.domain.agent.service import AgentMessage
from app.domain.session_record import service
from tests.unit.test_a_seat_is_read_when_its_session_writes import Counting, _said
from tests.unit.test_driven_liveness import Room, _until

WORK = str(uuid.uuid4())


def _said_records(texts: list[str]) -> list[dict]:
    stamp = {"work_id": WORK}
    records: list[dict] = [
        {
            "method": "turn/started",
            "params": {"threadId": "t", "turn": {"id": "turn-1"}},
            "cheese": stamp,
        }
    ]
    for n, text in enumerate(texts):
        records.append(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "t",
                    "item": {"type": "agentMessage", "id": f"m{n}", "text": text},
                },
                "cheese": stamp,
            }
        )
    return records


class _Session:
    def __init__(self, tmp_path: Path):
        self.runner = tmp_path / "runner.sqlite"
        self.mirror = tmp_path / "mirror.sqlite"
        self.ref = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese", harness=CODEX)
        self.heard: list[str] = []
        self.kept: list[dict] = []
        self.refusing = False

    def write(self, records: list[dict], *, at: datetime | None = None) -> None:
        journal = codex_journal.Journal(self.runner)
        try:
            for record in records:
                journal.append(record)
            if at is not None:
                with journal.connection:
                    journal.connection.execute(
                        "UPDATE events SET recorded_at = ?", (at.isoformat(),)
                    )
        finally:
            journal.close()

    async def drain(self) -> None:
        async def remote(method: str, params: dict) -> dict:
            journal = codex_journal.Journal(self.runner)
            try:
                return {"events": journal.read(params["after"])}
            finally:
                journal.close()

        async def consume(_p, _t, _w, event, *_rest) -> None:
            if isinstance(event, AgentMessage):
                self.heard.append(event.text)

        async def activity(*_args) -> None:
            return None

        async def records(_session, rows: list[dict]) -> None:
            if self.refusing:
                raise ConnectionError("the database is away")
            self.kept += rows

        reading = codex.Subscription(
            self.ref, self.mirror, remote, consume, activity, records=records
        )
        try:
            await reading.drain()
        finally:
            await reading.release()

    def kept_sequences(self) -> list[int]:
        return [row["sequence"] for row in self.kept]


async def test_the_room_hears_the_session_while_the_raw_record_is_away(tmp_path):
    session = _Session(tmp_path)
    session.write(_said_records(["looking at the loader", "found it"]))
    session.refusing = True
    await session.drain()
    assert session.heard == ["looking at the loader", "found it"]
    assert session.kept == []

    # Back again: what it missed is handed to it, once.
    session.refusing = False
    await session.drain()
    await session.drain()
    assert session.kept_sequences() == [1, 2, 3]
    assert session.heard == ["looking at the loader", "found it"]


async def test_the_mirror_keeps_what_the_raw_record_has_not_taken(tmp_path):
    session = _Session(tmp_path)
    two_days_ago = datetime.now(UTC) - timedelta(days=2)
    session.write(_said_records(["an old answer"]), at=two_days_ago)
    session.refusing = True
    await session.drain()
    assert session.kept == []

    # Retention runs over a mirror whose rows the room took long ago.
    CodexBacklog(session.mirror).forget(older_than_s=24 * 3600)

    session.refusing = False
    await session.drain()
    assert session.kept_sequences() == [1, 2]


def test_a_record_nested_deeper_than_python_recurses_is_kept_whole():
    """A tool result can nest deeper than the interpreter recurses; the record
    still has to be keepable, credentials out, every level there."""
    deep: dict = {"password": "hunter2-plain-text", "leaf": "bottom"}
    for _ in range(sys.getrecursionlimit() * 2):
        deep = {"child": deep}
    kept = service.scrubbed(deep)
    depth = 0
    while "child" in kept:
        kept = kept["child"]
        depth += 1
    assert depth == sys.getrecursionlimit() * 2
    assert kept == {"password": "***", "leaf": "bottom"}


def test_a_field_named_for_a_credential_loses_its_whole_value_and_only_that():
    kept = service.scrubbed(
        {
            "password": 84213377,
            "api_key": ["first half", "second half"],
            "Basic usage": "Run the tool with --help",
            "input_tokens": 1200,
        }
    )
    assert kept["password"] == "***"
    assert kept["api_key"] == "***"
    assert "Run the tool with --help" in kept.values()
    assert kept["input_tokens"] == 1200


async def test_a_session_the_platform_reads_hands_its_rows_to_the_raw_record():
    channel = Counting()
    kept: list[dict] = []

    async def records(_session, rows: list[dict]) -> None:
        kept.extend(rows)

    channel.host._records = records
    room = Room(channel)
    try:
        await room.send("fix the login page")
        await _until(lambda: _said(room) == ["on it"])
        await _until(
            lambda: any(
                "on it" in repr(row["record"]) and row["record"].get("cheese")
                for row in kept
            )
        )
    finally:
        await room.close()
