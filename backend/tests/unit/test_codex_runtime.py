"""Room delivery survives a reader replacement without sending another turn."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness import CODEX, SessionRef
from app.domain.agent.harness.channel import Placement
from app.domain.agent.harness.codex.journal import Journal
from app.domain.agent.harness.driven.runner import LONG_POLL
from app.domain.agent.room import sessions as room_sessions
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.service import AgentMessage, AgentResult
from app.domain.agent.session_host.contract import StartRefused
from app.domain.agent.session_host.host import SessionHost
from app.domain.agent_session.models import SessionPlace
from app.domain.agent_session.services import AgentSessionService
from app.domain.delivery.input_identity import InputIdentity, InputReceipt
from tests.support.room_reader import room_reader
from tests.support.seat_channel import SeatChannel


def hear(runtime, reader) -> None:
    runtime.report_to(reader, unread=lambda _topic: None, memory=AsyncMock())


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [DeviceCallError, DeviceOffline, TimeoutError])
async def test_discovery_releases_database_and_skips_a_dead_runner(failure):
    project = uuid.uuid4()
    sessions = [
        (
            project,
            uuid.uuid4(),
            "a",
            "codex",
            None,
            SessionPlace(
                machine="center",
                channel="central",
                resource_id=str(uuid.uuid4()),
                runtime={"harness": "codex", "state": state, "agent_handle": "a"},
                lease=None,
            ),
        )
        for state in ("$HOME/.cheese/harness/dead", "$HOME/.cheese/harness/alive")
    ]
    database_open = False

    @asynccontextmanager
    async def factory():
        nonlocal database_open
        database_open = True
        try:
            yield SimpleNamespace()
        finally:
            database_open = False

    async def ping(device, state, method, params, **kwargs):
        assert not database_open
        assert kwargs["timeout"] == 15
        if state.endswith("/dead"):
            raise failure("center")
        return {"alive": True, "thread_id": "retained", "capabilities": [LONG_POLL]}

    source = Mock(spec=CentralChannel)
    source.name = "central"
    source._session_factory = factory
    source.placed = CentralChannel.placed.__get__(source)
    hub = Mock(
        is_online=Mock(return_value=True), call_executor=AsyncMock(side_effect=ping)
    )
    runtime = RoomSessions(source, CODEX, SessionHost(hub))
    with patch.object(AgentSessionService, "placed_sessions", return_value=sessions):
        found = await runtime.recover("center")
    assert [session.topic_id for session in found] == [sessions[1][1]]
    (live,) = runtime.live.values()
    assert live.conversation == "retained"


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [DeviceCallError, DeviceOffline, TimeoutError])
async def test_recovery_continues_when_a_discovered_runner_disappears(
    tmp_path, failure
):
    project = uuid.uuid4()
    dead, alive = (
        SessionRef(project, uuid.uuid4(), "a", harness=CODEX) for _ in range(2)
    )

    class Seats(SeatChannel):
        async def open(self, session, agent, launch):
            raise AssertionError("recovery must not start a session")

        async def call(self, handle, method, params):
            assert method in {"ping", "events"}, "recovery must not send a prompt"
            if handle.session == dead:
                raise failure("center")
            if method == "events" and params.get("wait"):
                # Held, as a runner holds a read with nothing to answer it with.
                await asyncio.sleep(params["wait"])
            if method == "ping":
                return {
                    "alive": True,
                    "thread_id": "thread",
                    "capabilities": [LONG_POLL],
                    "turn_id": None,
                }
            return {"events": []}

    channel = Seats(harness=CODEX)
    for session in (dead, alive):
        channel.seats[(session.topic_id, "a")] = (
            session,
            f"$HOME/.cheese/harness/{session.topic_id}/a",
        )
    runtime = channel.runtime
    assert await runtime.recover("center") == [alive]
    assert not runtime.holds(dead.topic_id)
    assert runtime.holds(alive.topic_id)
    await runtime.close(alive)


@pytest.mark.anyio
async def test_room_send_steer_and_reconnect_keep_one_work_owner(tmp_path):
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "agent", harness=CODEX)
    work = uuid.uuid4()
    journal = Journal(tmp_path / "remote")
    inputs = []
    active = False

    async def call(handle, method, params):
        nonlocal active
        if method == "events":
            # Held, as a runner holds a read, until there is something past it.
            deadline = asyncio.get_running_loop().time() + params.get("wait", 0)
            while (
                not journal.read(params["after"])
                and asyncio.get_running_loop().time() < deadline
            ):
                await asyncio.sleep(0.01)
            return {"events": journal.read(params["after"])}
        if method == "ping":
            return {
                "alive": True,
                "thread_id": "thread",
                "capabilities": [LONG_POLL],
                "turn_id": "turn" if active else None,
            }
        if method == "interrupt":
            active = False
            return {"interrupted": True}
        assert method == "send"
        inputs.append(params)
        active = True
        journal.append(
            {
                "method": "turn/started",
                "params": {"threadId": "thread", "turn": {"id": "turn"}},
                "cheese": {"work_id": params["work_id"]},
            }
        )
        return {"turn_id": "turn"}

    class Seats(SeatChannel):
        async def open(self, session, agent, launch):
            pass

        async def call(self, handle, method, params):
            return await call(handle, method, params)

    channel = Seats(harness=CODEX)
    runtime = channel.runtime
    consumer = AsyncMock()
    receipts = AsyncMock()
    register_input = AsyncMock()
    hear(runtime, room_reader(events=consumer, receipts=receipts))
    marks = []
    replacement = None
    try:
        with patch.object(
            room_sessions.library, "read_attachment", return_value=b"fixture"
        ):
            assert await runtime.send(
                session,
                "first",
                system_prompt="system",
                work_id=work,
                on_mark=marks.append,
                register_input=register_input,
                images=[{"path": "uploads/image.png", "media_type": "image/png"}],
            )
        assert marks == [work]
        assert inputs[0]["input_id"] == str(work)
        assert inputs[0]["images"] == ["data:image/png;base64,Zml4dHVyZQ=="]
        assert await runtime.steer(
            session.topic_id, "steer", register_input=register_input
        )
        assert inputs[1]["work_id"] == str(work)
        assert inputs[1]["input_id"] != inputs[0]["input_id"]
        identities = [entry.args[0] for entry in register_input.await_args_list]
        assert identities == [
            InputIdentity(
                session.project_id,
                session.topic_id,
                "agent",
                "codex",
                "thread",
                work,
                work,
            ),
            InputIdentity(
                session.project_id,
                session.topic_id,
                "agent",
                "codex",
                "thread",
                uuid.UUID(inputs[1]["input_id"]),
                work,
            ),
        ]
        assert [entry.args for entry in receipts.await_args_list] == [
            (InputReceipt(identity, "accepted"),) for identity in identities
        ]
        # Lose only the backend reader. The remote process completes on its own.
        await runtime._detach(runtime._seat_of(session))
        for method, params in [
            (
                "item/completed",
                {
                    "threadId": "thread",
                    "item": {
                        "id": "reply",
                        "type": "agentMessage",
                        "text": "answer",
                    },
                },
            ),
            (
                "turn/completed",
                {
                    "threadId": "thread",
                    "turn": {
                        "id": "turn",
                        "status": "completed",
                        "items": [],
                    },
                },
            ),
        ]:
            journal.append(
                {
                    "method": method,
                    "params": params,
                    "cheese": {"work_id": str(work), "agent_handle": "agent"},
                }
            )
        replacement = channel.next_process()
        hear(replacement, room_reader(events=consumer))
        assert await replacement.recover() == [session]
        assert not replacement.tasks
        await replacement.replay(session)
        await replacement.replay(session)
        events = [entry.args[3] for entry in consumer.await_args_list]
        assert len([event for event in events if isinstance(event, AgentMessage)]) == 1
        assert len([event for event in events if isinstance(event, AgentResult)]) == 1
        assert len(inputs) == 2
        assert not replacement.work
        assert await replacement.interrupt(session)
    finally:
        await runtime._detach(runtime._seat_of(session))
        if replacement:
            await replacement._detach(replacement._seat_of(session))
        journal.close()


@pytest.mark.anyio
async def test_a_codex_that_did_not_start_says_one_sentence_and_keeps_its_stderr():
    stderr = (
        "Traceback (most recent call last):\n"
        '  File "<stdin>", line 40, in <module>\n'
        "FileNotFoundError: [Errno 2] No such file or directory: "
        "'/h/.cheese/tools/codex/node_modules/.bin/codex'\n"
        '  File "/usr/lib/python3.12/subprocess.py", line 1955, in _execute_child\n'
    )

    @asynccontextmanager
    async def prepare_session(*, runtime_factory, **_):
        runtime_factory(uuid.uuid4())
        yield SimpleNamespace(
            device_id="center",
            agent_user_id=1,
            agent_handle="a",
            token="t",
            env={
                "CHEESE_EXECUTION_TARGET": json.dumps(
                    {"kind": "device", "mcp_servers": []}
                ),
                "CHEESE_RESOURCE_ID": str(uuid.uuid4()),
            },
        )

    async def precheck(session, *, needs_place):
        return Placement("center", 1, "a", rented=False)

    async def api_base(device_id):
        return "http://backend"

    source = Mock(spec=CentralChannel)
    source.precheck = precheck
    source.prepare_session = prepare_session
    hub = Mock(
        is_online=Mock(return_value=True),
        exec=AsyncMock(return_value={"exit": 1, "stdout": "", "stderr": stderr}),
    )
    host = SessionHost(hub)
    host._api = api_base
    runtime = RoomSessions(source, CODEX, host)

    with pytest.raises(StartRefused) as refused:
        await runtime.ensure(
            SessionRef(uuid.uuid4(), uuid.uuid4(), "a", harness=CODEX),
            system_prompt="",
        )

    assert str(refused.value) == "Codex 启动失败：机器上缺少 codex"
    assert refused.value.log == stderr
