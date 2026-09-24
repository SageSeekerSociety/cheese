"""What a headless Claude Code session prints, as the room ends up seeing it.

Each test scripts the stream-json records one situation produces and follows
them through the real runner, journal mirror, translation and chat service: a
tool that failed, an input the build has not echoed yet, a long command while
somebody waits, a sub-thread's work, a runner that died, and a backend replaced
while its session went on working.
"""

import asyncio
import time
import uuid

from sqlalchemy import select

from app.api.deps import get_chat_service
from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.block.models import Block
from app.main import app
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.conftest import wait_work_idle as _wait_work_idle
from tests.integration.conftest import chat_ws_url, post_project, session_token


def _bearer(handle: str) -> dict:
    return {"Authorization": f"Bearer {session_token(handle)}"}


def _room(client, owner: str = "alice") -> str:
    project = post_project(client, {"name": "P", "owner_handle": owner}).json()["data"]
    return project["root_topic_id"]


def _until_done(ws) -> list[dict]:
    frames = []
    while True:
        frames.append(ws.receive_json())
        if frames[-1]["type"] in ("done", "error"):
            return frames


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


def _blocks(client, **where) -> list[Block]:
    async def read() -> list[Block]:
        async with client.test_factory() as session:
            query = select(Block).order_by(Block.created_at)
            for name, value in where.items():
                query = query.where(getattr(Block, name) == value)
            return list(await session.scalars(query))

    return asyncio.run(read())


def _written(stub: StubChannel, room: str) -> list[dict]:
    return [
        message
        for message in stub.sessions[uuid.UUID(room)].written
        if message.get("type") == "user"
    ]


def test_a_tool_that_failed_marks_its_step_with_what_it_said(client, stub_hooks):
    """A command that exited non-zero is a red step with the command's own words,
    not the build's ``Exit code`` header over them."""

    def turn(topic, prompt, reply):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Bash", eid="toolu_pandoc", command="pandoc a.md")
        stub_hooks.returns(
            topic,
            "Bash",
            "Exit code 127\nbash: pandoc: command not found",
            error=True,
        )
        stub_hooks.says(topic, "机器上没有 pandoc")
        stub_hooks.stops(topic, "机器上没有 pandoc")

    stub_hooks.emit_turn = turn
    room = _room(client)
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 转一下文档"})
        frames = _until_done(ws)
    assert frames[-1]["type"] == "done", frames[-1]
    _wait_work_idle()

    steps = [
        block
        for block in _blocks(client, topic_id=uuid.UUID(room))
        if (block.meta or {}).get("tool") == "Bash"
    ]
    assert len(steps) == 1
    assert steps[0].meta.get("failed") is True
    assert steps[0].meta.get("error") == "bash: pandoc: command not found"


def test_an_input_counts_as_received_only_once_the_session_echoes_it(
    client, stub_hooks
):
    """The build taking an input off its queue is not the build having it: only
    the echo of that exact input (``isReplay``) is the receipt."""
    chat = client.app.dependency_overrides[get_chat_service]()
    receipts: list[str] = []
    original = chat.confirm_prompt_receipt

    async def observe(topic_id, prompt):
        receipts.append(prompt)
        await original(topic_id, prompt)

    chat._compute.bind_receipts(observe)
    taken: list[str] = []

    def turn(topic, prompt, reply):
        session = stub_hooks.sessions[topic]
        identifier = next(
            m["uuid"] for m in reversed(session.written) if m.get("type") == "user"
        )
        taken.append(identifier)
        stub_hooks.starts(topic)
        stub_hooks.record(
            topic, type="command_lifecycle", command_uuid=identifier, state="started"
        )
        stub_hooks.says(topic, "在看")

    stub_hooks.emit_turn = turn
    room = _room(client)
    topic = uuid.UUID(room)
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 看一下"})
        _until(
            ws,
            lambda f: f["type"] == "event_block" and f["block"]["content"] == "在看",
        )
        assert receipts == [], "a queue record was taken for the receipt"

        stub_hooks.record(
            topic,
            type="user",
            uuid=taken[0],
            isReplay=True,
            parent_tool_use_id=None,
            message={"role": "user", "content": stub_hooks.last_prompt},
        )
        assert _wait_for(client, room, lambda: receipts), "the echo landed no receipt"
        stub_hooks.stops(topic, "看完了")
        assert _until_done(ws)[-1]["type"] == "done"
    assert len(receipts) == 1


def test_a_long_command_gets_the_progress_reminder_written_to_the_session(
    client, stub_hooks, monkeypatch
):
    """A person waiting through a 10-minute command is reminded about, and the
    reminder is written into the running session, where the build reads it at
    the command's end."""
    chat = client.app.dependency_overrides[get_chat_service]()

    def turn(topic, prompt, reply):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Bash", command="make test")

    stub_hooks.emit_turn = turn
    room = _room(client)
    topic = uuid.UUID(room)
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 跑一下测试"})
        _until(
            ws,
            lambda f: f["type"] == "event_block" and "make test" in str(f["block"]),
        )
        before = len(_written(stub_hooks, room))
        monkeypatch.setattr(settings, "chat_progress_reminder_after_s", 0)
        assert client.portal.call(chat.remind_silent_turns) == 1
        written = _written(stub_hooks, room)[before:]
        assert len(written) == 1, written
        assert "chat_send" in str(written[0]["message"]["content"])

        stub_hooks.returns(topic, "Bash", "42 passed")
        stub_hooks.stops(topic, "测试全过了")
        assert _until_done(ws)[-1]["type"] == "done"


def test_a_sub_threads_work_lands_on_its_card(client, stub_hooks):
    """Everything a worker does is on the card it was started for: the steps it
    prints on stdout, and those of an agent it starts in turn, which only that
    agent's own transcript file reports (the runner tails it). The room's
    controls hear that a task started."""
    room = _room(client)
    card = client.post(
        f"/topics/{room}/split",
        json={"title": "一条活", "brief": "干这个", "reviewer_handle": "alice"},
        headers=_bearer("alice"),
    ).json()["data"]

    def turn(topic, prompt, reply):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.spawns(topic, thread_label=card["thread_label"], call="call-1")
        stub_hooks.uses(topic, "Bash", parent="call-1", command="pytest -q")
        stub_hooks.returns(topic, "Bash", "3 passed", parent="call-1")
        stub_hooks.uses(
            topic,
            "Agent",
            eid="call-2",
            parent="call-1",
            description="细看一个文件",
            prompt="看看 a.py 里的分页",
            subagent_type="general-purpose",
        )
        stub_hooks.record(
            topic,
            type="system",
            subtype="task_started",
            task_id="nested-1",
            tool_use_id="call-2",
            task_type="local_agent",
            prompt="看看 a.py 里的分页",
        )
        stub_hooks.record(
            topic,
            type="cheese_file",
            agent_id="nested-1",
            entry={
                "type": "assistant",
                "uuid": str(uuid.uuid4()),
                "agentId": "nested-1",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_nested_read",
                            "name": "Read",
                            "input": {"file_path": "a.py"},
                        }
                    ],
                },
            },
        )
        stub_hooks.returns(topic, "Agent", "分页在第 40 行", call="call-1")
        stub_hooks.stops(topic, "派出去了")

    stub_hooks.emit_turn = turn
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 开干"})
        frames = _until_done(ws)
    assert frames[-1]["type"] == "done", frames[-1]
    _wait_work_idle()

    controls = [f for f in frames if f["type"] == "agent_control"]
    assert controls, "a task started and the room's controls were not told"
    assert "worker-1" in controls[0]["state"]["tasks"]

    on_card = {
        (block.meta or {}).get("tool")
        for block in _blocks(client, task_id=uuid.UUID(card["id"]))
    }
    assert {"Bash", "Read"} <= on_card, on_card
    in_room = [
        (block.meta or {}).get("tool")
        for block in _blocks(client, topic_id=uuid.UUID(room), task_id=None)
        if (block.meta or {}).get("tool") in ("Bash", "Read")
    ]
    assert in_room == [], "a worker's step landed on the room instead of its card"


def test_a_runner_that_died_mid_turn_ends_the_turn_where_the_room_sees_it(
    client, stub_hooks
):
    """The process is gone while a command runs: the room gets a failed turn it
    can see, not a turn that is running forever."""

    def turn(topic, prompt, reply):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Bash", command="make build")

    stub_hooks.emit_turn = turn
    room = _room(client)
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 编一下"})
        _until(
            ws,
            lambda f: f["type"] == "event_block" and "make build" in str(f["block"]),
        )
        stub_hooks.alive = False
        frames = _until_done(ws)
    assert frames[-1]["type"] == "error", frames[-1]
    assert "exited" in str(frames[-1])


class StillWorking(StubChannel):
    def emit_turn(self, topic_id, prompt, reply):
        del reply
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.uses(topic_id, "Bash", command="sleep 600")


def test_a_turn_survives_the_backend_being_replaced_under_it(client):
    """dev redeploys on every merge. A turn running on the machine at that moment
    is found again by the new process, and what it prints after lands in the
    room and ends the turn there."""
    project = post_project(client, {"name": "Restart"}).json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "换进程", "created_by": "alice"},
    ).json()["data"]["id"]
    topic = uuid.UUID(room)

    def service(channel: StubChannel) -> ChatService:
        return ChatService(
            session_factory=client.test_request_factory,
            base_system_prompt="你是芝士。",
            workspace_root="/tmp/claude-records-ws",
            compute=stub_compute(channel),
        )

    before = StillWorking()
    app.dependency_overrides[get_chat_service] = lambda: service(before)
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 睡一会"})
        _until(
            ws,
            lambda f: f["type"] == "event_block" and "sleep 600" in str(f["block"]),
        )
    client.portal.call(before.runtime._detach, topic)

    # The machine kept its runner; the new process has only the channel to it.
    after = StubChannel()
    after.root = before.root
    after.sessions = before.sessions
    for session in after.sessions.values():
        session.channel = after
    replaced = service(after)
    app.dependency_overrides[get_chat_service] = lambda: replaced
    assert client.portal.call(replaced.recover_sessions) == 1

    after.returns(topic, "Bash", "done", call=before.calls["Bash"])
    after.says(topic, "睡醒了")
    after.stops(topic, "睡醒了")
    client.portal.call(settle_turn, replaced, topic)

    said = [block.content for block in _blocks(client, topic_id=topic)]
    assert "睡醒了" in said
    assert _blocks(client, topic_id=topic, content="睡醒了")[0].turn_id is not None
