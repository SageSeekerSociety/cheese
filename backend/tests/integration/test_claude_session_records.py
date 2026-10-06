"""What a headless Claude Code session prints, as the room ends up seeing it.

Each test scripts the stream-json records one situation produces and follows
them through the real runner, journal mirror, translation and chat service: a
tool that failed, an input the build has not echoed yet, a long command while
somebody waits, a runner that died, and a backend replaced
while its session went on working.
"""

import asyncio
import time
import uuid

from sqlalchemy import select

from app.api.deps import get_chat_service
from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.models import AgentTurn
from app.domain.agent.room import reads as room_reads
from app.domain.block.models import Block
from app.domain.delivery.input_identity import InputIdentity, InputReceipt
from app.domain.delivery.models import NativeInput
from app.main import app
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.conftest import wait_work_idle as _wait_work_idle
from tests.integration.conftest import (
    chat_ws_url,
    in_thread,
    post_message,
    post_project,
    session_auth_headers,
)
from tests.support.room_reader import room_reader


def _room(client, owner: str = "alice") -> str:
    """A 支线 in the project's channel: where 芝士 answers when called."""
    project = post_project(client, {"name": "P"}, owner=owner).json()["data"]
    return in_thread(client, project["root_topic_id"], owner)


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
        for message in stub._session_for(uuid.UUID(room)).written
        if message.get("type") == "user"
    ]


def test_a_tool_that_failed_marks_its_step_with_what_it_said(client, stub_hooks):
    """A command that exited non-zero is a red step with the command's own words,
    not the build's ``Exit code`` header over them."""

    def turn(topic, prompt, reply, agent=None):
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
        post_message(client, room, "alice", {"content": "@芝士 转一下文档"})
        frames = _until_done(ws)
    assert frames[-1]["type"] == "done", frames[-1]
    _wait_work_idle()

    steps = [
        block
        for block in _blocks(client, conversation_id=uuid.UUID(room))
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
    receipts: list[InputReceipt] = []
    original = chat.confirm_prompt_receipt

    async def observe(receipt: InputReceipt):
        await original(receipt)
        if receipt.evidence == "native_echo":
            session = stub_hooks._session_for(receipt.identity.conversation_id)
            input_id = uuid.UUID(taken[0])
            assert receipt.identity == InputIdentity(
                session.project_id,
                session.topic_id,
                session.actor,
                CLAUDE_CODE,
                session.session_id,
                input_id,
                input_id,
            )
            assert receipt.execution_work_id == uuid.UUID(session.work)
            receipts.append(receipt)

    chat._compute.report_to(
        room_reader(receipts=observe, rest=room_reads.reader(chat)),
        unread=chat.oldest_unread_at,
        memory=chat._memory.sync,
    )
    taken: list[str] = []

    def turn(topic, prompt, reply, agent=None):
        session = stub_hooks._session_for(topic)
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
        post_message(client, room, "alice", {"content": "@芝士 看一下"})
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

    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Bash", command="make test")

    stub_hooks.emit_turn = turn
    room = _room(client)
    topic = uuid.UUID(room)
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        post_message(client, room, "alice", {"content": "@芝士 跑一下测试"})
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


def test_a_message_read_inside_the_running_turn_ends_with_it(
    client, stub_hooks, monkeypatch, caplog
):
    """A message that reaches the session while it is in the middle of a turn
    is read at that turn's next tool boundary and answered inside it. When that
    turn ends, so does the message's own: its turn has an end time, and the
    agent is not reminded about a person it has already answered."""
    chat = client.app.dependency_overrides[get_chat_service]()
    prompts: list[str] = []

    def turn(topic, prompt, reply, agent=None):
        prompts.append(prompt)
        if len(prompts) == 1:
            stub_hooks.starts(topic)
            stub_hooks.acknowledges(topic, prompt)
            stub_hooks.uses(topic, "Bash", command="make test")

    stub_hooks.emit_turn = turn
    room = _room(client)
    topic = uuid.UUID(room)
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        post_message(client, room, "alice", {"content": "@芝士 跑一下测试"})
        _until(
            ws,
            lambda f: f["type"] == "event_block" and "make test" in str(f["block"]),
        )

        # Use the running session's actual injection path. Hiding that path
        # exercises durable deferral, not a message read inside this work.
        before = len(_written(stub_hooks, room))
        post_message(client, room, "alice", {"content": "@芝士 顺便跑一下 lint"})
        assert _wait_for(client, room, lambda: len(_written(stub_hooks, room)) > before)
        injected = _written(stub_hooks, room)[-1]["message"]["content"]
        assert "顺便跑一下 lint" in injected
        # The stub calls emit_turn for each stdin write, including steer.
        assert len(prompts) == 2

        stub_hooks.returns(topic, "Bash", "42 passed")
        stub_hooks.acknowledges(topic, injected)
        stub_hooks.says(topic, "测试和 lint 都过了")
        stub_hooks.stops(topic, "测试和 lint 都过了")
        assert _until_done(ws)[-1]["type"] == "done"

    client.portal.call(settle_turn, chat, topic)

    async def assert_same_work():
        async with client.test_factory() as session:
            inputs = list(
                await session.scalars(
                    select(NativeInput).where(NativeInput.conversation_id == topic)
                )
            )
            assert len(inputs) == 2
            assert len({row.execution_work_id for row in inputs}) == 1
            assert all(row.echoed_at and row.completed_at for row in inputs)

    client.portal.call(assert_same_work)

    async def open_turns() -> list[AgentTurn]:
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(AgentTurn).where(
                        AgentTurn.conversation_id == topic,
                        AgentTurn.stopped_at.is_(None),
                    )
                )
            )

    assert _wait_for(client, room, lambda: not asyncio.run(open_turns())), [
        turn.content for turn in asyncio.run(open_turns())
    ]

    monkeypatch.setattr(settings, "chat_progress_reminder_after_s", 0)
    with caplog.at_level("INFO", logger="app.domain.agent.chat"):
        client.portal.call(chat.remind_silent_turns)
    assert "chat progress reminder topic=" not in caplog.text


def test_a_message_without_live_handoff_waits_for_completion_then_recovers(
    client, stub_hooks, monkeypatch
):
    """Losing the live handoff must not bypass unfinished durable work or lose
    the next message: committed completion resumes it without another summon."""
    chat = client.app.dependency_overrides[get_chat_service]()
    prompts: list[str] = []

    def turn(topic, prompt, reply, agent=None):
        prompts.append(prompt)
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        if len(prompts) == 1:
            stub_hooks.uses(topic, "Bash", command="make test")
        else:
            stub_hooks.says(topic, "lint 也过了")
            stub_hooks.stops(topic, "lint 也过了")

    stub_hooks.emit_turn = turn
    room = _room(client)
    topic = uuid.UUID(room)

    async def no_live_handoff(*args, **kwargs):
        return None

    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        post_message(client, room, "alice", {"content": "@芝士 跑一下测试"})
        _until(
            ws,
            lambda f: f["type"] == "event_block" and "make test" in str(f["block"]),
        )
        before = len(_written(stub_hooks, room))
        with monkeypatch.context() as hidden:
            hidden.setattr(chat, "merge_into_running_turn", no_live_handoff)
            second = post_message(
                client, room, "alice", {"content": "@芝士 顺便跑一下 lint"}
            )

            def deferred():
                blocks = _blocks(client, id=uuid.UUID(second["id"]))
                return blocks and (blocks[0].meta or {}).get("deferred_native_input")

            assert _wait_for(client, room, deferred)
            assert len(prompts) == 1
            assert len(_written(stub_hooks, room)) == before

        stub_hooks.returns(topic, "Bash", "42 passed")
        stub_hooks.stops(topic, "测试过了")
        assert _wait_for(client, room, lambda: len(prompts) == 2)

    client.portal.call(settle_turn, chat, topic)
    assert "顺便跑一下 lint" in prompts[1]

    async def completed_separately():
        async with client.test_factory() as session:
            inputs = list(
                await session.scalars(
                    select(NativeInput).where(NativeInput.conversation_id == topic)
                )
            )
            assert len(inputs) == 2
            assert len({row.execution_work_id for row in inputs}) == 2
            assert all(row.echoed_at and row.completed_at for row in inputs)
            turns = list(
                await session.scalars(
                    select(AgentTurn).where(AgentTurn.conversation_id == topic)
                )
            )
            assert len(turns) == 2
            assert all(row.stopped_at for row in turns)

    client.portal.call(completed_separately)


def test_a_runner_that_died_mid_turn_ends_the_turn_where_the_room_sees_it(
    client, stub_hooks
):
    """The process is gone while a command runs: the room gets a failed turn it
    can see, not a turn that is running forever."""

    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Bash", command="make build")

    stub_hooks.emit_turn = turn
    room = _room(client)
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        post_message(client, room, "alice", {"content": "@芝士 编一下"})
        _until(
            ws,
            lambda f: f["type"] == "event_block" and "make build" in str(f["block"]),
        )
        stub_hooks.alive = False
        frames = _until_done(ws)
    assert frames[-1]["type"] == "error", frames[-1]
    assert "exited" in str(frames[-1])


class StillWorking(StubChannel):
    def emit_turn(self, topic_id, prompt, reply, agent=None):
        del reply
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.uses(topic_id, "Bash", command="sleep 600")


def test_a_turn_survives_the_backend_being_replaced_under_it(client):
    """dev redeploys on every merge. A turn running on the machine at that moment
    is found again by the new process, and what it prints after lands in the
    room and ends the turn there."""
    project = post_project(client, {"name": "Restart"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "换进程"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    room = in_thread(client, room, "alice")
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
        post_message(client, room, "alice", {"content": "@芝士 睡一会"})
        _until(
            ws,
            lambda f: f["type"] == "event_block" and "sleep 600" in str(f["block"]),
        )
    # The old process stops reading, as a replaced backend does. `_detach` takes
    # a seat, not a room, so `_detach(room)` removed nothing and the old reader
    # went on handling the session's records next to the new one.
    client.portal.call(before.runtime.stop_listening)

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

    said = [block.content for block in _blocks(client, conversation_id=topic)]
    assert "睡醒了" in said
    assert (
        _blocks(client, conversation_id=topic, content="睡醒了")[0].turn_id is not None
    )


def _picked_up_by_a_new_backend(client) -> tuple[uuid.UUID, str, StubChannel]:
    """A room whose turn is running when its backend is replaced: the old
    process has let go of the session, and a new one has picked it up."""
    project = post_project(client, {"name": "Handover"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "交接"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    room = in_thread(client, room, "alice")
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
        post_message(client, room, "alice", {"content": "@芝士 睡一会"})
        _until(
            ws,
            lambda f: f["type"] == "event_block" and "sleep 600" in str(f["block"]),
        )
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
    return topic, room, after


def test_a_turn_the_next_backend_picks_up_still_answers_its_message(client):
    topic, _, after = _picked_up_by_a_new_backend(client)
    (asked,) = _blocks(client, conversation_id=topic, author="alice")

    after.returns(topic, "Bash", "done", call=after.calls["Bash"])
    after.says(topic, "睡醒了")
    after.stops(topic, "睡醒了")
    client.portal.call(settle_turn, app.dependency_overrides[get_chat_service](), topic)

    (answer,) = _blocks(client, conversation_id=topic, content="睡醒了")
    assert answer.reply_to == asked.id


def test_a_message_joins_the_turn_the_next_backend_picked_up(client):
    """Not a second turn started beside the one still running."""
    topic, room, _ = _picked_up_by_a_new_backend(client)

    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        post_message(client, room, "alice", {"content": "@芝士 顺便把闹钟关了"})
        _until_done(ws)

    async def turns() -> int:
        async with client.test_factory() as session:
            return len(
                list(
                    await session.scalars(
                        select(AgentTurn.id).where(AgentTurn.conversation_id == topic)
                    )
                )
            )

    assert asyncio.run(turns()) == 1


def test_a_teammates_turn_picked_up_by_the_next_backend_stays_the_teammates(
    client,
):
    """A room's non-default teammate is mid-turn when the backend is replaced.

    The new process must record the recovered turn as that teammate's seat.
    Recorded under the project default instead, a later message to the
    teammate finds no live turn of its own seat and starts a second turn
    beside the teammate's own (before seats, the same misattribution made it
    wait for 「another agent」 — which was the teammate itself; dev,
    2026-09-25, twice)."""
    from app.domain.agent_instance.services import AgentInstanceService
    from app.domain.identity.handles import agent_instance_handle
    from app.domain.topic_membership.services import TopicMemberService

    project = post_project(client, {"name": "Teammate"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "队友"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    channel_id = uuid.UUID(room)

    async def seat_teammate() -> None:
        async with client.test_factory() as session:
            made = await AgentInstanceService(session).create(
                project_id=uuid.UUID(project["id"]),
                handle="opus",
                type_name=None,
                display_name="Opus",
            )
            await TopicMemberService(session).ensure_agent_seat(
                channel_id, agent_instance_handle(made.id)
            )
            await session.commit()

    client.portal.call(seat_teammate)
    # The teammate is seated in the channel and answers in a 支线 of it.
    room = in_thread(client, room, "alice")
    topic = uuid.UUID(room)

    def service(channel: StubChannel) -> ChatService:
        return ChatService(
            session_factory=client.test_request_factory,
            base_system_prompt="你是芝士。",
            workspace_root="/tmp/claude-records-ws",
            compute=stub_compute(channel),
        )

    before = StillWorking()
    old = service(before)
    app.dependency_overrides[get_chat_service] = lambda: old
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        post_message(client, room, "alice", {"content": "@Opus 睡一会"})
        _until(
            ws,
            lambda f: f["type"] == "event_block" and "sleep 600" in str(f["block"]),
        )
    (running,) = old._hook_work.values()
    assert running.agent_instance_handle == "opus"
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

    (recovered,) = replaced._hook_work.values()
    assert recovered.agent_instance_handle == "opus"

    # A message to the teammate joins the recovered turn — it must not start
    # a second turn beside the teammate's own.
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        post_message(client, room, "alice", {"content": "@Opus 接着睡"})
        _until_done(ws)

    async def turns() -> int:
        async with client.test_factory() as session:
            return len(
                list(
                    await session.scalars(
                        select(AgentTurn.id).where(AgentTurn.conversation_id == topic)
                    )
                )
            )

    assert asyncio.run(turns()) == 1
