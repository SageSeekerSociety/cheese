"""A turn's raw record keeps what the agent did, as the harness wrote it.

The rule, stated without the code: whatever a session's harness journaled in a
turn — its thinking, a tool's whole output however long, the records of a
subagent it started — can be read back from the conversation afterwards,
unchanged except that credentials are taken out. And the record only grows:
reading the journal again, from a fresh mirror or after the records were moved
to the bucket, adds nothing twice, and what was there stays as it was.

Each case drives the real reading of the harness (Codex's mirror and
subscription, Claude Code's lifecycle reading) against a runner journal written
here, and reads back through the route a viewer uses.
"""

import base64
import functools
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.storage import StorageBackend
from app.domain.agent.harness import CLAUDE_CODE, CODEX, SessionRef
from app.domain.agent.harness.claude_code import journal as claude_journal
from app.domain.agent.harness.codex import journal as codex_journal
from app.domain.agent.harness.codex import subscription as codex
from app.domain.agent.harness.driven.reporting import Reading, Whose
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


def _claude_input(text: str, work: str) -> list[dict]:
    return [
        {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]},
            "cheese": {"work_id": work},
        }
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

    def get_url(self, key: str) -> str:
        return key

    async def list_keys(self, prefix: str):
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield key


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

    def drain(self, *, fresh_mirror: bool = False, pages: int = 1) -> None:
        """Drains by the backend; a fresh mirror reads the runner from the start.

        One drain takes one journal page (the journal's `PAGE`); a journal with
        more records than that needs as many drains to be taken whole.
        """
        if fresh_mirror or not self.mirrors:
            self.mirrors += 1
        mirror = self.tmp_path / f"mirror-{self.mirrors}.sqlite"

        async def remote(method: str, params: dict) -> dict:
            journal = self.journal_type(self.runner)
            try:
                if method == "read":
                    # A runner that reports in the lifecycle protocol: its
                    # records past what was taken, from the start for a reader
                    # that names nothing.
                    return {
                        "worker_epoch": 1,
                        "events": [],
                        "records": journal.read(params.get("records_after") or 0),
                        "state": {},
                    }
                assert method == "events"
                return {"events": journal.read(params["after"])}
            finally:
                journal.close()

        async def nothing(*_args, **_kwargs) -> None:
            return None

        keep = functools.partial(service.append, self.client.test_request_factory)
        reading: Reading | codex.Subscription
        if self.harness == CLAUDE_CODE:
            reading = Reading(
                Whose(self.ref, "cheese", self.ref.topic_id, "cheese", CLAUDE_CODE, ""),
                remote,
                epoch=lambda: 1,
                assemble=lambda record: [],
                show=nothing,
                hand=nothing,
                records=keep,
                controls=nothing,
            )
        else:
            reading = codex.Subscription(
                self.ref, mirror, remote, nothing, nothing, records=keep
            )

        async def run() -> None:
            try:
                for _ in range(pages):
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


def test_site_reads_full_raw_records_and_pages_the_same_turn(client, tmp_path):
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    work = str(uuid.uuid4())
    session.write(_claude_records(work))
    session.drain()
    blocks = []
    before = None
    while True:
        response = client.get(
            f"/topics/{session.ref.topic_id}/transcript",
            params={
                "limit": 2,
                **({"before": before} if before else {}),
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()["data"]
        blocks = result["data"] + blocks
        if not result["has_more"]:
            break
        before = result["oldest_id"]
    raw = [
        block["meta"]["record"]
        for block in blocks
        if "record" in (block.get("meta") or {})
    ]
    assert len(raw) == len(_claude_records(work))
    assert THINKING in json.dumps(raw, ensure_ascii=False)
    result = next(record for record in raw if record["type"] == "user")
    output = result["message"]["content"][0]["content"]
    assert output.startswith("HEAD OF THE OUTPUT")
    assert output.endswith("LAST LINE OF THE OUTPUT\n")
    assert SECRET not in output


def test_image_pixels_survive_mirroring_and_are_room_authorized(
    client, tmp_path, bucket, monkeypatch
):
    from app.api.routes import topics_session_record_images

    monkeypatch.setattr(topics_session_record_images, "private_storage", lambda: bucket)
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    pixels = b"\x89PNG\r\n\x1a\nexample-image"
    digest = hashlib.sha256(pixels).hexdigest()
    session.write(
        [
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.b64encode(pixels).decode(),
                            },
                        }
                    ]
                },
                "cheese": {"work_id": str(uuid.uuid4())},
            }
        ]
    )
    session.drain()
    source = session.read()[0]["record"]["message"]["content"][0]["source"]
    assert source["sha256"] == digest
    path = f"/topics/{session.ref.topic_id}/session-record-images/{digest}"
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.content == pixels
    assert response.headers["content-type"] == "image/png"
    stranger = {"Authorization": "", **session_auth_headers("stranger")}
    assert client.get(path, headers=stranger).status_code in (401, 403, 404)
    other = _room_for(client)
    assert (
        client.get(f"/topics/{other}/session-record-images/{digest}").status_code == 404
    )
    item = session.read()[0]
    cursor = service.encode_cursor(
        (
            datetime.fromisoformat(item["at"]),
            item["harness"],
            item["agent_handle"],
            item["sequence"],
        )
    )
    exact = f"/topics/{session.ref.topic_id}/session-records/{cursor}"
    assert client.get(exact).status_code == 200
    owner = {"Authorization": "", **session_auth_headers("user-1")}
    assert (
        client.delete(f"/topics/{session.ref.topic_id}", headers=owner).status_code
        == 200
    )
    for endpoint in (path, exact, f"/topics/{session.ref.topic_id}/transcript"):
        assert client.get(endpoint).status_code == 404


def test_agent_history_tools_read_search_and_page_complete_raw_records(
    client, tmp_path
):
    import re

    from tests.unit.test_platform_tool_runner import cheese as tools

    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    session.write(_claude_records(str(uuid.uuid4())))
    session.drain()

    class Host:
        environ = {"CHEESE_TOPIC": str(session.ref.topic_id)}

        def request(self, plan):
            response = client.request(plan["method"], plan["path"])
            assert response.status_code == 200, response.text
            return response.json()

    host = Host()
    found = tools.run_platform_tool(
        "cheese_chat",
        {"action": "search", "kind": "event", "query": "HEAD OF THE OUTPUT"},
        host,
    )
    record_id = re.search(r"id=(record:[A-Za-z0-9_-]+)", found)[1]
    result = ""
    offset = 0
    while True:
        text = tools.run_platform_tool(
            "cheese_chat",
            {"action": "get", "message_id": record_id, "offset": offset},
            host,
        )
        result += text
        more = re.search(r"offset=(\d+)", text)
        if more is None:
            break
        offset = int(more[1])
    assert "HEAD OF THE OUTPUT" in result
    assert "LAST LINE OF THE OUTPUT" in result
    assert SECRET not in result


def test_channel_record_search_crosses_task_conversations_without_paging_gaps(
    client, tmp_path
):
    from tests.integration.conftest import open_task
    from tests.unit.test_platform_tool_runner import cheese as tools

    room = _room_for(client)
    task = open_task(client, str(room), owner="user-1")
    root = _Session(client, tmp_path, CLAUDE_CODE, room)
    child_path = tmp_path / "task"
    child_path.mkdir()
    child = _Session(client, child_path, CLAUDE_CODE, uuid.UUID(task["id"]))

    def record(label):
        return {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": f"channel-evidence {label}"}]
            },
            "cheese": {"work_id": str(uuid.uuid4())},
        }

    root.write([record("root")])
    child.write([record("child-one"), record("child-two")])
    root.drain()
    child.drain()
    for newest, direction in ((True, "before"), (False, "after")):
        cursor = None
        found = []
        while True:
            response = client.get(
                f"/topics/{room}/session-records",
                params={
                    "channel": True,
                    "newest": newest,
                    "q": "channel-evidence",
                    "limit": 1,
                    **({direction: cursor} if cursor else {}),
                },
            )
            assert response.status_code == 200, response.text
            page = response.json()["data"]
            found += [
                item["record"]["message"]["content"][0]["text"]
                for item in page["items"]
            ]
            cursor = page["next"]
            if cursor is None:
                break
        assert sorted(found) == [
            "channel-evidence child-one",
            "channel-evidence child-two",
            "channel-evidence root",
        ]

    class Host:
        environ = {"CHEESE_TOPIC": str(room)}

        def request(self, plan):
            response = client.request(plan["method"], plan["path"])
            assert response.status_code == 200, response.text
            return response.json()

    result = tools.run_platform_tool(
        "cheese_chat",
        {
            "action": "search",
            "kind": "event",
            "query": "channel-evidence child-two",
            "channel": True,
        },
        Host(),
    )
    assert "channel-evidence child-two" in result


def test_prelaunch_turn_has_one_explanation_and_no_legacy_details(client):
    from app.domain.agent.models import AgentTurn
    from app.domain.block.models import Block, BlockKind
    from app.domain.conversation.models import Conversation
    from app.domain.event_author import AuthorType

    room = _room_for(client)

    async def seed():
        async with client.test_request_factory() as db:
            conversation = await db.get(Conversation, room)
            turn = uuid.uuid4()
            before = datetime.now(UTC) - timedelta(days=1)
            db.add(
                AgentTurn(
                    id=turn,
                    conversation_id=room,
                    continuation_id=turn,
                    author="user-1",
                    content="Earlier work",
                    agent_handle="cheese",
                    started_at=before,
                    stopped_at=before,
                )
            )
            db.add(
                Block(
                    conversation_id=room,
                    project_id=conversation.project_id,
                    author="cheese",
                    author_type=AuthorType.participant,
                    kind=BlockKind.event,
                    content="Retired tool detail",
                    meta={"tool": "Bash", "in_room": False},
                    turn_id=turn,
                )
            )
            await db.commit()

    client.portal.call(seed)
    response = client.get(f"/topics/{room}/transcript")
    assert response.status_code == 200, response.text
    items = response.json()["data"]["data"]
    assert len(items) == 1
    assert items[0]["meta"]["before_launch"] is True
    assert "Retired tool detail" not in json.dumps(items)


def test_a_turn_after_the_launch_is_never_called_pre_launch(client):
    """A turn that ran after the room began keeping raw records, with none of
    its own to show (not written yet, or expired), is not explained away as
    one from before the launch."""
    from app.domain.agent.models import AgentTurn
    from app.domain.conversation.models import Conversation

    room = _room_for(client)

    async def seed():
        async with client.test_request_factory() as db:
            conversation = await db.get(Conversation, room)
            after = conversation.raw_records_started_at + timedelta(minutes=1)
            turn = uuid.uuid4()
            db.add(
                AgentTurn(
                    id=turn,
                    conversation_id=room,
                    continuation_id=turn,
                    author="user-1",
                    content="Work since the launch",
                    agent_handle="cheese",
                    started_at=after,
                    stopped_at=after,
                )
            )
            await db.commit()

    client.portal.call(seed)
    response = client.get(f"/topics/{room}/transcript")
    assert response.status_code == 200, response.text
    items = response.json()["data"]["data"]
    assert not any(item["meta"].get("before_launch") for item in items), items


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


@pytest.mark.parametrize("harness", [CLAUDE_CODE, CODEX])
def test_the_lines_read_out_is_what_a_transcript_holds(client, tmp_path, harness):
    """`lines=true` gives one row per record, the same keys whichever harness
    wrote it, and the subagent's thread named."""
    session = _Session(client, tmp_path, harness, _room_for(client))
    work = str(uuid.uuid4())
    session.write(session.records(work))
    session.drain()

    response = client.get(
        f"/topics/{session.ref.topic_id}/session-records",
        params={"lines": True, "turn_id": work, "limit": 500},
    )
    assert response.status_code == 200, response.text
    rows = response.json()["data"]["items"]
    assert rows
    assert len({tuple(sorted(row)) for row in rows}) == 1
    assert all(row["turn"] == work for row in rows)
    assert all(row["harness"] == harness for row in rows)
    # The subagent's moment is in the same file, marked as its own thread.
    assert any(row["thread"] == "toolu_task" for row in rows) or any(
        row["parent_thread"] for row in rows
    )
    # A tool call and its return are readable without the raw record.
    assert any(
        part["kind"] == "tool"
        for row in rows
        for part in row["parts"]
        if part.get("name")
    )
    # Raw records are not in this answer; they are what a caller asks without
    # `lines` for.
    assert all("record" not in row for row in rows)


def test_the_platforms_repeated_words_are_not_in_the_file(client, tmp_path):
    """What an input carries is the person's words and the platform's own; a
    transcript is written to be read, so only the person's come through. Claude
    Code is the harness that journals an input at all."""
    from app.domain.agent.harness.notices import (
        platform_prompt,
        publication_prompt,
        silence_reminder,
    )

    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    work = str(uuid.uuid4())
    session.write(
        _claude_input(
            publication_prompt("照我说的做")
            + "\n\n"
            + platform_prompt(silence_reminder(10)),
            work,
        )
    )
    session.drain()

    rows = client.get(
        f"/topics/{session.ref.topic_id}/session-records",
        params={"lines": True, "turn_id": work, "limit": 500},
    ).json()["data"]["items"]
    said = [part["text"] for row in rows for part in row["parts"]]
    assert "照我说的做" in said
    assert not any("分钟了还没回" in text for text in said)
    # The journal still holds every word: this is the file's projection only.
    whole = json.dumps(session.read(turn_id=work), ensure_ascii=False)
    assert "分钟了还没回" in whole


def test_the_line_read_hands_out_only_this_conversation(client, tmp_path):
    """A transcript is this conversation's; another room's records are not
    reachable through its topic, and a stranger reaches neither."""
    mine = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    mine.write(_claude_records(str(uuid.uuid4())))
    mine.drain()
    other = tmp_path / "other"
    other.mkdir()
    theirs = _Session(client, other, CLAUDE_CODE, _room_for(client))
    theirs.write(_claude_records(str(uuid.uuid4())))
    theirs.drain()

    rows = client.get(
        f"/topics/{mine.ref.topic_id}/session-records", params={"lines": True}
    ).json()["data"]["items"]
    assert rows
    assert {row["conversation"] for row in rows} == {None}
    assert all(row["author"] == mine.ref.agent_handle for row in rows)

    stranger = {"Authorization": "", **session_auth_headers("a-stranger")}
    for params in ({}, {"lines": True}):
        refused = client.get(
            f"/topics/{mine.ref.topic_id}/session-records",
            params=params,
            headers=stranger,
        )
        assert refused.status_code in (401, 403, 404), refused.text


def test_who_may_not_read_the_room_may_not_read_its_record(client, tmp_path):
    """The raw record holds whole tool outputs: someone outside the room is
    refused it exactly as they are refused the room's 现场."""
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    session.write(_claude_records(str(uuid.uuid4())))
    session.drain()
    assert session.read()
    room = session.ref.topic_id
    stranger = {"Authorization": "", **session_auth_headers("a-stranger")}
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
