"""A turn's raw record keeps what the agent did, as the harness wrote it.

The rule, stated without the code: whatever a session's harness journaled in a
turn — its thinking, a tool's whole output however long, the records of a
subagent it started — can be read back from the conversation afterwards,
unchanged except that credentials are taken out. And the record only grows:
reading the journal again, from a fresh mirror or after the records were moved
to the bucket, adds nothing twice, and what was there stays as it was.

Each case drives the real mirror and subscription of the harness against a
runner journal written here, and reads back through the route a viewer uses.
"""

import functools
import json
from datetime import UTC, datetime, timedelta
import uuid
from pathlib import Path
from typing import BinaryIO

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.storage import StorageBackend
from app.domain.agent.harness import CLAUDE_CODE, CODEX, SessionRef
from app.domain.agent.harness.claude_code import journal as claude_journal
from app.domain.agent.harness.claude_code import subscription as claude
from app.domain.agent.harness.codex import journal as codex_journal
from app.domain.agent.harness.codex import subscription as codex
from app.domain.session_record import service
from tests.integration.conftest import session_auth_headers
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room

SECRET = "ghp_" + "Zx81kQ3mN5pR7tV9wY2aB4cD6eF8gH0jK1lM"
THINKING = "The failing test reads the config twice; check the loader before the cache."
#: A credential a tool was handed as a field of its own, not inside a string.
FIELD_SECRET = "correct horse, " + "battery staple"
#: A tool's output far past the 8 KiB the timeline keeps: its head, a
#: credential, a NUL (binary output carries them), 60 KB of lines, then its
#: last line.
OUTPUT = (
    "HEAD OF THE OUTPUT\n"
    f"export GITHUB_TOKEN={SECRET}\n"
    "binary \x00 byte\n"
    + "".join(f"line {n:05d} of the build log\n" for n in range(2500))
    + "LAST LINE OF THE OUTPUT\n"
)
SUBAGENT_SAID = "The subagent found the loader reads the file twice."


def _claude_records(work: str) -> list[dict]:
    stamp = {"work_id": work}
    return [
        {"type": "system", "subtype": "init", "session_id": "s1", "cheese": stamp},
        {
            "type": "assistant",
            "uuid": str(uuid.uuid4()),
            "message": {
                "id": "msg_1",
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": THINKING, "signature": "sig"},
                ],
            },
            "parent_tool_use_id": None,
            "cheese": stamp,
        },
        {
            "type": "assistant",
            "uuid": str(uuid.uuid4()),
            "message": {
                "id": "msg_1",
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_bash",
                        "name": "Bash",
                        "input": {
                            "command": "make test",
                            "description": "Run tests",
                            "env": {"DB_PASSWORD": FIELD_SECRET},
                        },
                    }
                ],
            },
            "parent_tool_use_id": None,
            "cheese": stamp,
        },
        {
            "type": "user",
            "uuid": str(uuid.uuid4()),
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_bash",
                        "content": OUTPUT,
                    }
                ],
            },
            "parent_tool_use_id": None,
            "cheese": stamp,
        },
        {
            "type": "assistant",
            "uuid": str(uuid.uuid4()),
            "message": {
                "id": "msg_2",
                "role": "assistant",
                "content": [{"type": "text", "text": SUBAGENT_SAID}],
            },
            "parent_tool_use_id": "toolu_task",
            "cheese": stamp,
        },
    ]


def _codex_records(work: str) -> list[dict]:
    stamp = {"work_id": work}
    main, child = "thread-main", "thread-child"
    return [
        {
            "method": "turn/started",
            "params": {"threadId": main, "turn": {"id": "turn-1"}},
            "cheese": stamp,
        },
        {
            "method": "item/completed",
            "params": {
                "threadId": main,
                "item": {
                    "type": "reasoning",
                    "id": "r1",
                    "summary": [THINKING],
                    "content": [],
                },
            },
            "cheese": stamp,
        },
        {
            "method": "item/completed",
            "params": {
                "threadId": main,
                "item": {
                    "type": "dynamicToolCall",
                    "id": "call-1",
                    "tool": "bash",
                    "arguments": {"command": "make test", "api_key": FIELD_SECRET},
                    "success": True,
                    "contentItems": [{"type": "inputText", "text": OUTPUT}],
                },
            },
            "cheese": stamp,
        },
        {
            "method": "thread/started",
            "params": {
                "thread": {
                    "id": child,
                    "parentThreadId": main,
                    "agentRole": "explorer",
                }
            },
            "cheese": stamp,
        },
        {
            "method": "item/completed",
            "params": {
                "threadId": child,
                "item": {"type": "agentMessage", "id": "m1", "text": SUBAGENT_SAID},
            },
            "cheese": stamp,
        },
    ]


HARNESSES = {
    CLAUDE_CODE: (claude_journal.Journal, _claude_records),
    CODEX: (codex_journal.Journal, _codex_records),
}


class _Bucket(StorageBackend):
    """The private bucket, in memory."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def upload(self, file: BinaryIO, key: str, content_type: str) -> str:
        self.objects[key] = file.read()
        return key

    async def download(self, key: str) -> bytes | None:
        return self.objects.get(key)

    async def delete(self, key: str) -> bool:
        return self.objects.pop(key, None) is not None

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def delete_prefix(self, prefix: str) -> int:
        keys = [key for key in self.objects if key.startswith(prefix)]
        for key in keys:
            del self.objects[key]
        return len(keys)

    def get_url(self, key: str) -> str:
        return key


class _Session:
    """One harness session: a runner journal, and the backend mirroring it."""

    def __init__(self, client, tmp_path: Path, harness: str, topic: uuid.UUID):
        self.client, self.tmp_path, self.harness = client, tmp_path, harness
        self.journal_type, self.records = HARNESSES[harness]
        self.runner = tmp_path / "runner.sqlite"
        self.ref = SessionRef(uuid.uuid4(), topic, "cheese", harness=harness)
        self.mirrors = 0

    def write(self, records: list[dict]) -> None:
        journal = self.journal_type(self.runner)
        try:
            for record in records:
                journal.append(record)
        finally:
            journal.close()

    def drain(self, *, fresh_mirror: bool = False) -> None:
        """One drain by the backend; a fresh mirror reads the runner from the start."""
        if fresh_mirror or not self.mirrors:
            self.mirrors += 1
        mirror = self.tmp_path / f"mirror-{self.mirrors}.sqlite"

        async def remote(method: str, params: dict) -> dict:
            assert method == "events"
            journal = self.journal_type(self.runner)
            try:
                return {"events": journal.read(params["after"])}
            finally:
                journal.close()

        async def nothing(*_args, **_kwargs) -> None:
            return None

        keep = functools.partial(service.append, self.client.test_request_factory)
        if self.harness == CLAUDE_CODE:
            reading = claude.Subscription(
                self.ref,
                mirror,
                remote,
                nothing,
                nothing,
                session_id="s1",
                recipient_handle="cheese",
                announce=nothing,
                receipts=nothing,
                completions=nothing,
                terminations=nothing,
                records=keep,
            )
        else:
            reading = codex.Subscription(
                self.ref, mirror, remote, nothing, nothing, records=keep
            )

        async def run() -> None:
            try:
                await reading.drain()
            finally:
                await reading.release()

        self.client.portal.call(run)

    def read(self, **params) -> list[dict]:
        items: list[dict] = []
        after = None
        while True:
            query = {**params, "limit": 2, **({"after": after} if after else {})}
            r = self.client.get(
                f"/topics/{self.ref.topic_id}/session-records", params=query
            )
            assert r.status_code == 200, r.text
            page = r.json()["data"]
            items += page["items"]
            if page["next"] is None:
                return items
            after = page["next"]


@pytest.fixture
def bucket(monkeypatch) -> _Bucket:
    bucket = _Bucket()
    monkeypatch.setattr(service, "private_storage", lambda: bucket)
    return bucket


def _room_for(client) -> uuid.UUID:
    project = _project(client, "raw record")
    return uuid.UUID(_room(client, project, "raw record room"))


def _seal(client, bucket: _Bucket) -> int:
    later = datetime.now(UTC) + service.SEAL_AFTER * 2

    async def run() -> int:
        return await service.seal(client.test_request_factory, bucket, now=later)

    return client.portal.call(run)


@pytest.mark.parametrize("harness", [CLAUDE_CODE, CODEX])
def test_a_turn_keeps_its_thinking_whole_output_and_subagent_scrubbed(
    client, tmp_path, bucket, harness
):
    session = _Session(client, tmp_path, harness, _room_for(client))
    work = str(uuid.uuid4())
    written = session.records(work)
    session.write(written)
    session.drain()

    kept = session.read(turn_id=work)
    assert [item["record"]["cheese"]["work_id"] for item in kept] == [work] * len(
        written
    )
    as_text = repr([item["record"] for item in kept])
    assert THINKING in as_text
    assert SUBAGENT_SAID in as_text
    assert "HEAD OF THE OUTPUT" in as_text
    assert "line 00000 of the build log" in as_text
    assert "LAST LINE OF THE OUTPUT" in as_text
    assert SECRET not in as_text
    assert FIELD_SECRET not in as_text
    # Apart from the credentials, and the NUL PostgreSQL cannot store, the
    # records are the ones the harness wrote.
    assert [item["record"] for item in kept] == [
        _as_stored(record) for record in written
    ]

    # Moved to the bucket, the record reads back the same.
    assert _seal(client, bucket) >= 1
    assert bucket.objects
    assert session.read(turn_id=work) == kept


def test_the_raw_record_only_grows(client, tmp_path, bucket):
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    work = str(uuid.uuid4())
    session.write(_claude_records(work))
    session.drain()
    first = session.read()
    assert len(first) == len(_claude_records(work))

    # The same journal read again — by the same mirror, and by a mirror that
    # starts over — lands nothing twice.
    session.drain()
    session.drain(fresh_mirror=True)
    assert session.read() == first

    # Nor once the records are in the bucket.
    _seal(client, bucket)
    session.drain(fresh_mirror=True)
    assert session.read() == first

    # What the session does next is added after; what was there is unchanged.
    later = [
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "session_id": "s1",
            "cheese": {"work_id": work},
        }
    ]
    session.write(later)
    session.drain()
    now = session.read()
    assert now[: len(first)] == first
    assert [item["record"]["type"] for item in now[len(first) :]] == ["result"]

    # And a stored record cannot be rewritten in place.
    async def rewrite() -> None:
        async with client.test_request_factory() as db:
            await db.execute(text("UPDATE session_records SET records = '[]'::jsonb"))
            await db.commit()

    with pytest.raises(DBAPIError, match="append-only"):
        client.portal.call(rewrite)
    assert session.read() == now


def test_what_the_room_steps_over_is_still_kept(client, tmp_path):
    """Output read hours late never reaches the room (it has moved on), but the
    session still did it."""
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    work = str(uuid.uuid4())
    hours_ago = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    journal = claude_journal.Journal(session.runner)
    try:
        with journal.connection:
            for record in _claude_records(work):
                journal.connection.execute(
                    "INSERT INTO records(recorded_at, record) VALUES (?, ?)",
                    (hours_ago, json.dumps(record)),
                )
    finally:
        journal.close()
    session.drain()
    kept = session.read(turn_id=work)
    assert len(kept) == len(_claude_records(work))
    assert THINKING in repr(kept)


def test_a_record_nested_deep_is_kept_and_read_back(client, tmp_path):
    """An MCP result can nest far deeper than a person would write; the session
    still did it, and the record still has to read back."""
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    work = str(uuid.uuid4())
    records = _claude_records(work)
    expected = [_as_stored(r) for r in records]
    deep: dict = {"leaf": "bottom"}
    for _ in range(1200):
        deep = {"child": deep}
    for each in (records, expected):
        each[2]["message"]["content"][0]["input"]["result"] = deep
    session.write(records)
    session.drain()
    kept = session.read(turn_id=work)
    # Compared as JSON text, keys sorted (JSONB keeps no key order): comparing
    # the objects themselves would recurse past the limit.
    assert json.dumps([i["record"] for i in kept], sort_keys=True) == json.dumps(
        expected, sort_keys=True
    )


def test_who_may_not_read_the_room_may_not_read_its_record(client, tmp_path):
    """The raw record holds whole tool outputs: someone outside the room is
    refused it exactly as they are refused the room's 现场."""
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    session.write(_claude_records(str(uuid.uuid4())))
    session.drain()
    assert session.read()
    room = session.ref.topic_id
    stranger = {"X-Cheese-Token": "", **session_auth_headers("a-stranger")}
    site = client.get(f"/topics/{room}/transcript", headers=stranger)
    record = client.get(f"/topics/{room}/session-records", headers=stranger)
    assert site.status_code in (401, 403, 404), site.text
    assert record.status_code == site.status_code, record.text


def _as_stored(value):
    """What a record should read back as: both credentials masked, NUL replaced."""
    if isinstance(value, str):
        if value == FIELD_SECRET:
            return "***"
        return value.replace(SECRET, "***").replace("\x00", "�")
    if isinstance(value, dict):
        return {k: _as_stored(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_as_stored(v) for v in value]
    return value
