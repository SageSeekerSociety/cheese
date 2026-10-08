"""A compaction is one record in the 现场, whichever backend hears it end.

dev replaces its backend on every merge, and a session compacting its context
runs straight through that: the compaction starts while one backend is
listening and ends while another is. The room must still show one line for it,
and that line must stop saying 「正在整理」 once the compaction is over. Before,
the new backend knew nothing of the line the old one landed: it landed a
second one, and the first said 「正在整理」 for good (FB-55).
"""

import asyncio
import time
import uuid

from sqlalchemy import select

from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.domain.block.models import Block
from app.domain.run_record.models import RunRecord
from app.main import app
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    room_socket,
)
from tests.support.run_records import records_of


class Compacts(StubChannel):
    """A session that, given a prompt, starts work and begins compacting."""

    def emit_turn(self, topic_id, prompt, reply, agent=None):
        del reply, agent
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.record(topic_id, type="system", subtype="status", status="compacting")


def _compaction_lines(client, topic: uuid.UUID) -> list[RunRecord]:
    """The compaction's records in the 现场; it is never said in the room."""

    async def read() -> list[RunRecord]:
        async with client.test_factory() as session:
            said = await session.scalars(
                select(Block).where(Block.conversation_id == topic)
            )
            assert not [
                b for b in said if (b.meta or {}).get("event_type") == "context_compact"
            ]
            return await records_of(session, topic, "context_compact")

    return asyncio.run(read())


def _state(line: RunRecord) -> str | None:
    return (line.meta or {}).get("state")


def _wait_for(client, room: str, fn, *, tries: int = 300):
    for _ in range(tries):
        client.get(f"/topics/{room}")
        if got := fn():
            return got
        time.sleep(0.02)
    return fn()


def _until(ws, predicate) -> None:
    while not predicate(ws.receive_json()):
        pass


def _replace_backend(
    client, before: StubChannel, service
) -> tuple[StubChannel, ChatService]:
    """The old process stops reading, as a replaced backend does; the machine
    keeps its runner, and a new process picks the session up."""
    client.portal.call(before.runtime.stop_listening)
    after = StubChannel()
    after.root = before.root
    after.sessions = before.sessions
    after.calls = before.calls
    for session in after.sessions.values():
        session.channel = after
    replaced = service(after)
    app.dependency_overrides[get_chat_service] = lambda: replaced
    assert client.portal.call(replaced.recover_sessions) == 1
    return after, replaced


def _room(client) -> tuple[str, uuid.UUID]:
    project = post_project(client, {"name": "Compact"}, owner="alice").json()["data"]
    room = in_thread(client, project["root_topic_id"], "alice")
    return room, uuid.UUID(room)


def _service(client):
    def build(channel: StubChannel) -> ChatService:
        return ChatService(
            session_factory=client.test_request_factory,
            base_system_prompt="你是芝士。",
            workspace_root="/tmp/compaction-across-backends-ws",
            compute=stub_compute(channel),
        )

    return build


def _start_compacting(client, room: str, service) -> StubChannel:
    before = Compacts()
    old_backend = service(before)
    app.dependency_overrides[get_chat_service] = lambda: old_backend
    with room_socket(client, room, "alice") as ws:
        post_message(client, room, "alice", {"content": "@芝士 接着做"})
        _until(
            ws,
            lambda f: (
                f["type"] == "run_record"
                and (f["record"]["meta"] or {}).get("event_type") == "context_compact"
            ),
        )
    return before


def test_a_compaction_that_ends_on_the_next_backend_is_one_line_and_over(client):
    room, topic = _room(client)
    service = _service(client)
    before = _start_compacting(client, room, service)

    after, replaced = _replace_backend(client, before, service)
    # The new backend hears the compaction still going (the harness says so
    # again, or tries again), then hears it end.
    after.record(topic, type="system", subtype="status", status="compacting")
    after.record(
        topic, type="system", subtype="status", status=None, compact_result="success"
    )
    after.says(topic, "接上了")
    after.stops(topic, "接上了")
    client.portal.call(settle_turn, replaced, topic)

    _wait_for(
        client,
        room,
        lambda: any(
            _state(line) == "over" for line in _compaction_lines(client, topic)
        ),
    )
    lines = _compaction_lines(client, topic)
    assert len(lines) == 1, [(line.content, _state(line)) for line in lines]
    assert _state(lines[0]) == "over"
    # Over because it finished, not because the turn ended around it.
    assert (lines[0].meta or {}).get("detail") is None


def test_a_turn_that_ends_on_the_next_backend_mid_compaction_leaves_nothing_running(
    client,
):
    room, topic = _room(client)
    service = _service(client)
    before = _start_compacting(client, room, service)

    after, replaced = _replace_backend(client, before, service)
    after.stops(topic, "")
    client.portal.call(settle_turn, replaced, topic)

    def running() -> list[str]:
        return [
            line.content
            for line in _compaction_lines(client, topic)
            if _state(line) != "over"
        ]

    assert _wait_for(client, room, lambda: not running()), running()
    assert len(_compaction_lines(client, topic)) == 1
