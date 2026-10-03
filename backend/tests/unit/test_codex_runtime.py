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
from app.domain.agent.harness import AgentRuntime, Opening, SessionRef
from app.domain.agent.harness.channel import Placement, ScreenSetupError
from app.domain.agent.harness.codex.channel import CodexChannel
from app.domain.agent.harness.codex.journal import Journal
from app.domain.agent.harness.codex.runtime import CodexRuntime, Handle
from app.domain.agent.harness.driven.runner import LONG_POLL
from app.domain.agent.harness.launch import ExecutorLaunch
from app.domain.agent.service import AgentMessage, AgentResult
from app.domain.agent_session.models import SessionPlace
from app.domain.agent_session.services import AgentSessionService
from app.domain.delivery.input_identity import InputIdentity, InputReceipt
from tests.support.room_reader import room_reader


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
        for state in ("/dead", "/alive")
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
        if state == "/dead":
            raise failure("center")
        return {"alive": True, "thread_id": "retained"}

    source = Mock(spec=CentralChannel)
    source.name = "central"
    source.provisions_machine = True
    source._session_factory = factory
    source._hub = Mock(
        is_online=Mock(return_value=True), call_executor=AsyncMock(side_effect=ping)
    )
    channel = CodexChannel(source, Mock(spec=ExecutorLaunch))
    with patch.object(AgentSessionService, "placed_sessions", return_value=sessions):
        found = await channel.discover("center")
    assert [handle.session.topic_id for handle in found] == [sessions[1][1]]
    assert found[0].thread_id == "retained"


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [DeviceCallError, DeviceOffline, TimeoutError])
async def test_recovery_continues_when_a_discovered_runner_disappears(
    tmp_path, failure
):
    project = uuid.uuid4()
    handles = [
        Handle(
            SessionRef(project, uuid.uuid4(), harness="codex"),
            "center",
            state,
            "thread",
            "a",
            tmp_path / state[1:],
            frozenset({LONG_POLL}),
        )
        for state in ("/dead", "/alive")
    ]

    async def call(handle, method, params):
        assert method in {"ping", "events"}, "recovery must not send a prompt"
        if handle == handles[0]:
            raise failure("center")
        if method == "events" and params.get("wait"):
            # Held, as a runner holds a read with nothing to answer it with.
            await asyncio.sleep(params["wait"])
        return {"turn_id": None} if method == "ping" else {"events": []}

    channel = AsyncMock(
        discover=AsyncMock(return_value=handles), call=AsyncMock(side_effect=call)
    )
    runtime = CodexRuntime(channel)
    assert await runtime.recover("center") == [handles[1].session]
    assert not runtime.holds(handles[0].session.topic_id)
    assert runtime.holds(handles[1].session.topic_id)
    await runtime.close(handles[1].session)


@pytest.mark.anyio
async def test_room_send_steer_and_reconnect_keep_one_work_owner(tmp_path):
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "agent", harness="codex")
    work = uuid.uuid4()
    handle = Handle(
        session,
        "center",
        "/state",
        "thread",
        "agent",
        tmp_path / "mirror",
        frozenset({LONG_POLL}),
    )
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
            return {"turn_id": "turn" if active else None}
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

    channel = AsyncMock()
    channel.ensure.return_value = handle
    channel.images.return_value = ["data:image/png;base64,fixture"]
    channel.call.side_effect = call
    channel.discover.return_value = [handle]
    runtime = CodexRuntime(channel)
    assert isinstance(runtime, AgentRuntime)
    consumer = AsyncMock()
    receipts = AsyncMock()
    register_input = AsyncMock()
    runtime.bind_reader(room_reader(events=consumer, receipts=receipts))
    marks = []
    replacement = None
    try:
        assert await runtime.send(
            session,
            "first",
            Opening("system"),
            work_id=work,
            on_mark=marks.append,
            register_input=register_input,
            images=[{"path": "uploads/image.png"}],
        )
        assert marks == [work]
        assert inputs[0]["input_id"] == str(work)
        assert inputs[0]["images"] == ["data:image/png;base64,fixture"]
        assert await runtime.deliver(
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
        replacement = CodexRuntime(channel)
        replacement.bind_reader(room_reader(events=consumer))
        assert await replacement.recover() == [session]
        assert not replacement.tasks
        await replacement.replay(session, known_texts=set())
        await replacement.replay(session, known_texts={"answer"})
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
    source._device_api_base = api_base
    source._hub = Mock(
        exec=AsyncMock(return_value={"exit": 1, "stdout": "", "stderr": stderr})
    )
    channel = CodexChannel(source, Mock(spec=ExecutorLaunch))

    with pytest.raises(ScreenSetupError) as refused:
        await channel.ensure(
            SessionRef(uuid.uuid4(), uuid.uuid4(), "a", harness="codex"),
            Opening(system_prompt=""),
        )

    assert str(refused.value) == "Codex 启动失败：机器上缺少 codex"
    assert refused.value.log == stderr
