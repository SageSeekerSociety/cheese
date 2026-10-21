"""What a room's socket hears while a turn runs, for 现场 to show it live.

现场 appends what the socket brings instead of waiting to be reopened, so every
change to its timeline has to cross the socket: a step that failed, a request
the session is retrying, a machine the turn is waiting for. Each test scripts
the stream-json records one situation produces and follows them through the
real runner, mirror, translation and chat service to the frames a browser
receives and the blocks a reload reads back.
"""

import asyncio
import uuid

from sqlalchemy import select

from app.domain.agent.device_hub import DeviceOffline
from app.domain.agent.session_host import host as session_host
from app.domain.block.models import Block
from app.domain.run_record.models import RunRecord
from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)
from tests.support.run_records import record_frames, records_of


def _alice() -> dict:
    return session_auth_headers("alice")


def _room(client) -> str:
    """A 支线 of the project's 综合: where 芝士 answers when called."""
    project = post_project(client, {"name": "P"}, owner="alice").json()
    return in_thread(client, project["data"]["root_topic_id"], "alice")


def _until(ws, predicate) -> list[dict]:
    frames = []
    while True:
        frames.append(ws.receive_json())
        if predicate(frames[-1]):
            return frames


def _done(frame: dict) -> bool:
    return frame["type"] in ("done", "error")


def _blocks(client, room: str) -> list[Block]:
    async def read() -> list[Block]:
        async with client.test_factory() as session:
            query = (
                select(Block)
                .where(Block.conversation_id == uuid.UUID(room))
                .order_by(Block.created_at)
            )
            return list(await session.scalars(query))

    return asyncio.run(read())


def _of_type(blocks: list[Block], event_type: str) -> list[Block]:
    return [b for b in blocks if (b.meta or {}).get("event_type") == event_type]


def _recorded(client, room: str, kind: str) -> list[RunRecord]:
    """What the platform recorded about the room's running: kept for the 现场,
    and never a line in the conversation itself."""

    async def read() -> list[RunRecord]:
        async with client.test_factory() as session:
            return await records_of(session, room, kind)

    assert _of_type(_blocks(client, room), kind) == []
    return asyncio.run(read())


def _retry(stub, topic, attempt: int) -> None:
    stub.record(
        topic,
        type="system",
        subtype="api_retry",
        attempt=attempt,
        max_retries=10,
        retry_delay_ms=500,
        error_status=429,
        error="rate_limit",
    )


def test_tool_output_does_not_create_or_rewrite_event_blocks(client, stub_hooks):
    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Bash", command="pandoc a.md")
        stub_hooks.returns(topic, "Bash", "bash: pandoc: command not found", error=True)
        stub_hooks.stops(topic, "No pandoc")

    stub_hooks.emit_turn = turn
    room = _room(client)
    with room_socket(client, room, "alice") as ws:
        post_message(client, room, "alice", {"content": "@芝士 convert"})
        frames = _until(ws, _done)
    assert not [
        f
        for f in frames
        if f["type"] in ("event_block", "block_updated")
        and (f["block"].get("meta") or {}).get("tool")
    ]
    assert not [b for b in _blocks(client, room) if (b.meta or {}).get("tool")]


def test_a_streak_of_retries_is_one_line_that_counts_them(client, stub_hooks):
    """Three retries of the same request read as one line saying 3/10, not as
    three lines. Once the request goes through, the next retry is new news and
    gets a line of its own."""

    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        for attempt in (1, 2, 3):
            _retry(stub_hooks, topic, attempt)
        stub_hooks.says(topic, "查到了")
        _retry(stub_hooks, topic, 1)
        stub_hooks.stops(topic, "好了")

    stub_hooks.emit_turn = turn
    room = _room(client)
    with room_socket(client, room, "alice") as ws:
        post_message(client, room, "alice", {"content": "@芝士 查一下"})
        frames = _until(ws, _done)
    wait_work_idle()

    retries = _recorded(client, room, "api_retry")
    assert [b.meta["attempt"] for b in retries] == [1, 2, 3, 1]
    assert "3/10" in retries[2].content
    assert retries[0].meta["max_attempts"] == 10
    assert "429" in retries[0].meta["detail"]
    # Both records belong to the turn and to the agent doing it, which is what
    # 现场 groups and filters on.
    assert all(b.turn_id is not None for b in retries)
    assert all(b.seat is not None for b in retries)

    sent = record_frames(frames, "api_retry")
    assert len({r["id"] for r in sent}) == 4
    assert [r["meta"]["attempt"] for r in sent] == [1, 2, 3, 1]


def test_a_retry_that_got_no_response_says_so(client, stub_hooks):
    """Claude Code files a request that got no response at all as ``unknown``
    with no HTTP status. The line says what that means — nothing came back, and
    for how long — instead of showing the bare word."""

    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.record(
            topic,
            type="system",
            subtype="api_retry",
            attempt=1,
            max_retries=10,
            retry_delay_ms=500,
            error_status=None,
            error="unknown",
            no_response={"waited_ms": 30000, "retry_wait_ms": 500},
        )
        stub_hooks.stops(topic, "好了")

    stub_hooks.emit_turn = turn
    room = _room(client)
    with room_socket(client, room, "alice") as ws:
        post_message(client, room, "alice", {"content": "@芝士 查一下"})
        _until(ws, _done)
    wait_work_idle()

    [retry] = _recorded(client, room, "api_retry")
    assert "30 秒" in retry.meta["detail"]
    assert "没收到" in retry.meta["detail"]
    assert "unknown" not in retry.meta["detail"]


def _compaction(stub, topic, **status) -> None:
    stub.record(topic, type="system", subtype="status", **status)


def test_a_compaction_says_so_while_it_runs_and_when_it_is_over(client, stub_hooks):
    """Compacting a long conversation can take minutes with nothing said and no
    reply to new messages. The room hears that it started, on one line, and the
    same line says when it is over."""

    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        _compaction(stub_hooks, topic, status="compacting")
        _compaction(stub_hooks, topic, status=None, compact_result="success")
        stub_hooks.says(topic, "接上了")
        stub_hooks.stops(topic, "好了")

    stub_hooks.emit_turn = turn
    room = _room(client)
    with room_socket(client, room, "alice") as ws:
        post_message(client, room, "alice", {"content": "@芝士 接着做"})
        frames = _until(ws, _done)
    wait_work_idle()

    lines = _recorded(client, room, "context_compact")
    assert len(lines) == 2
    assert lines[0].meta["state"] == "running"
    assert lines[-1].meta["state"] == "over"
    assert "整理" in lines[0].content
    assert lines[0].turn_id is not None

    sent = record_frames(frames, "context_compact")
    assert len({r["id"] for r in sent}) == 2
    assert [r["meta"]["state"] for r in sent] == ["running", "over"]


def test_a_compaction_that_fails_says_why(client, stub_hooks):
    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        _compaction(stub_hooks, topic, status="compacting")
        _compaction(
            stub_hooks,
            topic,
            status=None,
            compact_result="failed",
            compact_error="prompt too long",
        )
        stub_hooks.stops(topic, "好了")

    stub_hooks.emit_turn = turn
    room = _room(client)
    with room_socket(client, room, "alice") as ws:
        post_message(client, room, "alice", {"content": "@芝士 接着做"})
        _until(ws, _done)
    wait_work_idle()

    first, line = _recorded(client, room, "context_compact")
    assert first.meta["state"] == "running"
    assert line.meta["state"] == "over"
    assert line.meta["detail"] == "prompt too long"


def test_a_turn_that_ends_mid_compaction_does_not_leave_it_running(client, stub_hooks):
    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        _compaction(stub_hooks, topic, status="compacting")
        stub_hooks.stops(topic, "")

    stub_hooks.emit_turn = turn
    room = _room(client)
    with room_socket(client, room, "alice") as ws:
        post_message(client, room, "alice", {"content": "@芝士 接着做"})
        _until(ws, _done)
    wait_work_idle()

    first, line = _recorded(client, room, "context_compact")
    assert first.meta["state"] == "running"
    assert line.meta["state"] == "over"


def test_a_status_record_about_something_else_says_nothing(client, stub_hooks):
    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        _compaction(stub_hooks, topic, status=None, permissionMode="default")
        stub_hooks.stops(topic, "好了")

    stub_hooks.emit_turn = turn
    room = _room(client)
    with room_socket(client, room, "alice") as ws:
        post_message(client, room, "alice", {"content": "@芝士 接着做"})
        _until(ws, _done)
    wait_work_idle()

    assert _recorded(client, room, "context_compact") == []


def test_a_turn_waiting_for_its_machine_says_so_and_says_when_it_is_back(
    client, stub_hooks, monkeypatch
):
    """While the device is out of reach nothing the session does can arrive, so
    the 现场 records that it is waiting — once — and the same record says when
    the machine is back."""
    # The waiting line is written once the runner's lease on the machine has
    # lapsed (``host.LEASE_S``, two minutes). The behaviour under test is the
    # record, not that wait: shorten it rather than paying it in the suite.
    monkeypatch.setattr(session_host, "LEASE_S", 0.05)
    offline = {"now": False}
    reach = stub_hooks.call

    async def call(handle, method, params):
        if offline["now"]:
            raise DeviceOffline("the device has no live link")
        return await reach(handle, method, params)

    monkeypatch.setattr(stub_hooks, "call", call)

    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Bash", command="make build")

    stub_hooks.emit_turn = turn
    room = _room(client)
    topic = uuid.UUID(room)
    with room_socket(client, room, "alice") as ws:
        post_message(client, room, "alice", {"content": "@芝士 编一下"})
        _until(
            ws,
            lambda f: f["type"] == "turn_started",
        )
        offline["now"] = True
        waiting = _until(
            ws,
            lambda f: (
                f["type"] == "run_record"
                and (f["record"]["meta"] or {}).get("event_type") == "device_waiting"
            ),
        )[-1]["record"]
        offline["now"] = False
        back = _until(
            ws,
            lambda f: (
                f["type"] == "run_record"
                and (f["record"].get("meta") or {}).get("event_type")
                == "device_waiting"
                and f["record"]["meta"].get("state") == "over"
            ),
        )[-1]["record"]
        stub_hooks.returns(topic, "Bash", "built")
        stub_hooks.stops(topic, "编好了")
        _until(ws, _done)
    wait_work_idle()

    assert waiting["meta"]["state"] == "waiting"
    assert "offline" in waiting["meta"]["detail"]
    assert back["meta"]["state"] == "over"
    assert back["id"] != waiting["id"]
    assert len(_recorded(client, room, "device_waiting")) == 2


def test_a_socket_that_joins_mid_turn_learns_when_the_turn_started(client, stub_hooks):
    """A browser opened halfway through a turn has to be able to say how long it
    has been going, not how long it has been watching."""

    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Bash", command="sleep 60")

    stub_hooks.emit_turn = turn
    room = _room(client)
    topic = uuid.UUID(room)
    with room_socket(client, room, "alice") as ws:
        post_message(client, room, "alice", {"content": "@芝士 等一下"})
        started = _until(ws, lambda f: f["type"] == "turn_started")[-1]
        with room_socket(client, room, "alice") as late:
            active = _until(late, lambda f: f["type"] == "turn_active")[-1]
        stub_hooks.stops(topic, "好了")
        _until(ws, _done)

    assert active["turn_ids"] == [started["turn_id"]]
    assert set(active["since"]) == {started["turn_id"]}
    assert isinstance(active["since"][started["turn_id"]], float)
