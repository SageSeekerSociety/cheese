"""A platform message read inside a running turn ends with that turn, whichever
backend hears the turn end.

dev replaces its backend on every merge, and a turn runs straight through
that. A platform message (a scheduled check-in, a CI nudge) is a turn of its
own; sent while the session is working on something of its own, it is read at
that work's next tool boundary and answered there. When the backend that saw
it read is replaced before that work ends, it must still end with the work, or
its room stays busy for good.
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.api.deps import get_chat_service, get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.agent.models import AgentTurn
from app.domain.agent.runtime import addressed_to_agent
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


def test_a_check_in_read_before_the_backend_changed_ends_with_that_work(client):
    project = post_project(client, {"name": "Handover"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "交接"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    # 芝士 answers in a 支线: that is the conversation its session works in.
    room = in_thread(client, room, "alice")
    topic = uuid.UUID(room)

    def service(channel: StubChannel) -> ChatService:
        return ChatService(
            session_factory=client.test_request_factory,
            base_system_prompt="你是芝士。",
            workspace_root="/tmp/taken-across-backends-ws",
            compute=stub_compute(channel),
        )

    before = FirstPromptOnly()
    # One service per backend, as in production: what the session says
    # arrives on the subscription owned by the service that started it.
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

        # Then the session goes to work on its own, nobody having asked.
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
        # The old backend has seen the session read it inside its own work.
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

    # The old process stops reading, as a replaced backend does; the machine
    # kept its runner, and a new process picks the session up.
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

    after.returns(topic, "Bash", "done", call=after.calls["Bash"])
    after.says(topic, "进度正常")
    after.stops(topic, "进度正常")
    client.portal.call(settle_turn, replaced, topic)

    def still_running() -> list[str]:
        return [
            turn.content
            for turn in _rows(client, AgentTurn, topic, AgentTurn.stopped_at.is_(None))
        ]

    assert _wait_for(client, room, lambda: not still_running()), still_running()


def test_a_check_in_read_inside_the_sessions_work_leaves_nothing_running(client):
    """On one backend too. The call that sent the check-in returns once the
    session has taken it over, closing the check-in's interval before the work
    that read it ends. It still ends with that work: its input settled, and
    nothing of it is left counted as running in the room."""
    project = post_project(client, {"name": "Mid"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "中途"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    # 芝士 answers in a 支线: that is the conversation its session works in.
    room = in_thread(client, room, "alice")
    topic = uuid.UUID(room)
    channel = FirstPromptOnly()
    chat = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/taken-same-backend-ws",
        compute=stub_compute(channel),
    )
    app.dependency_overrides[get_chat_service] = lambda: chat
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        post_message(client, room, "alice", {"content": "@芝士 看一眼 CI"})
        assert _wait_for(
            client, room, lambda: channel.sessions and _written(channel, room)
        )
        channel.says(topic, "CI 是绿的")
        channel.stops(topic, "CI 是绿的")
        _until(ws, lambda f: f["type"] == "done")

        channel.starts(topic)
        channel.uses(topic, "Bash", command="sleep 600")
        _until(
            ws,
            lambda f: f["type"] == "event_block" and "sleep 600" in str(f["block"]),
        )
        seat = next(
            turn.agent_handle
            for turn in _rows(client, AgentTurn, topic)
            if turn.agent_handle
        )
        sent = len(_written(channel, room))

        async def check_in():
            return get_work_runner().submit(
                chat,
                topic,
                author="system",
                content="巡检：看一眼进度",
                addressed=addressed_to_agent(seat),
            )

        check = client.portal.call(check_in)
        assert _wait_for(client, room, lambda: len(_written(channel, room)) > sent)
        channel.acknowledges(topic, _written(channel, room)[-1]["message"]["content"])
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

        async def call_returned():
            async with client.test_request_factory() as session:
                await session.execute(
                    update(AgentTurn)
                    .where(AgentTurn.id == check)
                    .values(stopped_at=datetime.now(UTC))
                )
                await session.commit()

        client.portal.call(call_returned)
        channel.returns(topic, "Bash", "done", call=channel.calls["Bash"])
        channel.says(topic, "进度正常")
        channel.stops(topic, "进度正常")
    client.portal.call(settle_turn, chat, topic)

    def left() -> list:
        return [
            *(
                ("turn", turn.content)
                for turn in _rows(
                    client, AgentTurn, topic, AgentTurn.stopped_at.is_(None)
                )
            ),
            *(
                ("input", str(row.work_id))
                for row in _rows(
                    client, NativeInput, topic, NativeInput.work_id == check
                )
                if not row.completed_at
            ),
            *(("running", str(key[1])) for key in chat._hook_work if key[0] == topic),
            *(("active", str(work)) for work in chat._active_turn_ids.get(topic, ())),
        ]

    assert _wait_for(client, room, lambda: not left()), left()
