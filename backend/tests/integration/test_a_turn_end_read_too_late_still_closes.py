"""A turn whose end was read too late still ends, and the room hears nothing
of it.

A record nobody read for hours is stepped over rather than landed: by then the
room has gone on without it. A turn's end is the exception. The session is
still alive, so nothing else ends the turn — and an open turn keeps its room
busy for good, along with every message read inside it.
"""

import asyncio
import sqlite3
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.api.deps import get_chat_service, get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.agent.harness.driven.subscription import STALE_S
from app.domain.agent.models import AgentTurn
from app.domain.agent.runtime import addressed_to_agent
from app.domain.block.models import Block
from app.domain.delivery.models import NativeInput
from app.main import app
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.integration.conftest import (
    chat_ws_url,
    in_thread,
    post_message,
    post_project,
    session_auth_headers,
)


class FirstPromptOnly(StubChannel):
    """A session that answers its first prompt as a turn; every later prompt
    is read at a tool boundary of whatever it is doing then."""

    def emit_turn(self, topic_id, prompt, reply, agent=None):
        del reply
        self.prompts = getattr(self, "prompts", 0) + 1
        if self.prompts == 1:
            self.starts(topic_id)
            self.acknowledges(topic_id, prompt)


def _until(ws, predicate) -> dict:
    while True:
        frame = ws.receive_json()
        if predicate(frame):
            return frame


def _wait_for(client, room: str, fn, *, tries: int = 300):
    for _ in range(tries):
        client.get(f"/topics/{room}")
        if got := fn():
            return got
        time.sleep(0.02)
    return fn()


def _written(stub: StubChannel, room: str) -> list[dict]:
    return [
        message
        for message in stub._session_for(uuid.UUID(room)).written
        if message.get("type") == "user"
    ]


def _rows(client, model, topic: uuid.UUID, *where) -> list:
    async def read() -> list:
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(model).where(model.conversation_id == topic, *where)
                )
            )

    return asyncio.run(read())


def _age_records_after(stub: StubChannel, topic: uuid.UUID, sequence: int) -> int:
    """Make what the session recorded after ``sequence`` older than the reader
    lands: as if the backend had been away, or stuck, that long."""
    journal = stub._session_for(topic).state / "records.sqlite"
    then = (datetime.now(UTC) - timedelta(seconds=STALE_S + 600)).isoformat()
    with sqlite3.connect(journal, timeout=10) as connection:
        return connection.execute(
            "UPDATE records SET recorded_at = ? WHERE sequence > ?", (then, sequence)
        ).rowcount


def _last_sequence(stub: StubChannel, topic: uuid.UUID) -> int:
    journal = stub._session_for(topic).state / "records.sqlite"
    with sqlite3.connect(journal, timeout=10) as connection:
        (last,) = connection.execute("SELECT MAX(sequence) FROM records").fetchone()
    return last or 0


def test_a_turn_whose_end_was_read_hours_late_still_ends_and_says_nothing(client):
    project = post_project(client, {"name": "Late"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "迟到"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    # 芝士 answers in a 支线 of the channel.
    room = in_thread(client, room, "alice")
    topic = uuid.UUID(room)

    def service(channel: StubChannel) -> ChatService:
        return ChatService(
            session_factory=client.test_request_factory,
            base_system_prompt="你是芝士。",
            workspace_root="/tmp/late-turn-end-ws",
            compute=stub_compute(channel),
        )

    before = FirstPromptOnly()
    old_backend = service(before)
    app.dependency_overrides[get_chat_service] = lambda: old_backend
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        post_message(client, room, "alice", {"content": "@芝士 看一眼 CI"})
        assert _wait_for(
            client, room, lambda: before.sessions and _written(before, room)
        )
        before.says(topic, "CI 是绿的")
        before.stops(topic, "CI 是绿的")
        _until(ws, lambda f: f["type"] == "done")

        # The session goes to work on its own, and a check-in sent meanwhile
        # is read inside that work.
        before.starts(topic)
        before.uses(topic, "Bash", command="sleep 600")
        _until(
            ws,
            lambda f: f["type"] == "event_block" and "sleep 600" in str(f["block"]),
        )
        seat = next(
            turn.agent_handle
            for turn in _rows(client, AgentTurn, topic)
            if turn.agent_handle
        )
        sent = len(_written(before, room))

        async def check_in():
            return get_work_runner().submit(
                old_backend,
                topic,
                author="system",
                content="巡检：看一眼进度",
                addressed=addressed_to_agent(seat),
            )

        check = client.portal.call(check_in)
        assert _wait_for(client, room, lambda: len(_written(before, room)) > sent)
        before.acknowledges(topic, _written(before, room)[-1]["message"]["content"])
        assert _wait_for(
            client,
            room,
            lambda: _rows(
                client,
                NativeInput,
                topic,
                NativeInput.work_id == check,
                NativeInput.echoed_at.is_not(None),
            ),
        )

    # The backend stops reading. The session finishes its work while nobody
    # reads it, and the next backend first reads that ending hours later.
    client.portal.call(before.runtime.stop_listening)
    unread_from = _last_sequence(before, topic)
    before.returns(topic, "Bash", "done", call=before.calls["Bash"])
    before.says(topic, "进度正常")
    before.stops(topic, "进度正常")
    assert _age_records_after(before, topic, unread_from) == 3

    after = StubChannel()
    after.root = before.root
    after.sessions = before.sessions
    after.calls = before.calls
    for session in after.sessions.values():
        session.channel = after
    replaced = service(after)
    app.dependency_overrides[get_chat_service] = lambda: replaced
    assert client.portal.call(replaced.recover_sessions) == 1
    client.portal.call(settle_turn, replaced, topic)

    def still_running() -> list[str]:
        return [
            turn.content
            for turn in _rows(client, AgentTurn, topic, AgentTurn.stopped_at.is_(None))
        ]

    assert _wait_for(client, room, lambda: not still_running()), still_running()
    # Nothing of the late ending reached the room: not its reply, not a line
    # saying it ended.
    said = [block.content for block in _rows(client, Block, topic)]
    assert not any("进度正常" in (content or "") for content in said), said
