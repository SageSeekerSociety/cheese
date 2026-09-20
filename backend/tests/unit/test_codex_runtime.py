"""Room delivery survives a reader replacement without sending another turn."""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness import AgentRuntime, Opening, SessionRef
from app.domain.agent.harness.codex.channel import CodexChannel
from app.domain.agent.harness.codex.journal import Journal
from app.domain.agent.harness.codex.runtime import CodexRuntime, Handle
from app.domain.agent.harness.launch import ExecutorLaunch
from app.domain.agent.service import AgentMessage, AgentResult


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [DeviceCallError, DeviceOffline, TimeoutError])
async def test_discovery_releases_database_and_skips_a_dead_runner(failure):
    project = uuid.uuid4()
    rooms = [
        SimpleNamespace(
            id=uuid.uuid4(),
            project_id=project,
            session_placement={
                "device_id": "center",
                "resource_id": str(uuid.uuid4()),
                "channel": "central",
                "runtime": {"harness": "codex", "state": state, "agent_handle": "a"},
            },
        )
        for state in ("/dead", "/alive")
    ]
    database_open = False

    @asynccontextmanager
    async def factory():
        nonlocal database_open
        database_open = True
        try:
            yield SimpleNamespace(scalars=AsyncMock(return_value=rooms))
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
    found = await channel.discover("center")
    assert [handle.session.topic_id for handle in found] == [rooms[1].id]
    assert found[0].thread_id == "retained"


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [DeviceCallError, DeviceOffline, TimeoutError])
async def test_recovery_continues_when_a_discovered_runner_disappears(
    tmp_path, failure
):
    project = uuid.uuid4()
    handles = [
        Handle(
            SessionRef(project, uuid.uuid4()),
            "center",
            state,
            "thread",
            "a",
            tmp_path / state[1:],
        )
        for state in ("/dead", "/alive")
    ]

    async def call(handle, method, params):
        assert method in {"ping", "events"}, "recovery must not send a prompt"
        if handle == handles[0]:
            raise failure("center")
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
    session = SessionRef(uuid.uuid4(), uuid.uuid4())
    work = uuid.uuid4()
    handle = Handle(session, "center", "/state", "thread", "agent", tmp_path / "mirror")
    journal = Journal(tmp_path / "remote")
    inputs = []
    active = False

    async def call(handle, method, params):
        nonlocal active
        if method == "events":
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
    runtime.bind_events(consumer)
    runtime.bind_receipts(receipts)
    marks = []
    replacement = None
    try:
        assert await runtime.send(
            session,
            "first",
            Opening("system"),
            work_id=work,
            on_mark=marks.append,
            images=[{"path": "uploads/image.png"}],
        )
        assert marks == [work]
        assert inputs[0]["input_id"] == str(work)
        assert inputs[0]["images"] == ["data:image/png;base64,fixture"]
        assert await runtime.deliver(session.topic_id, "steer")
        assert inputs[1]["work_id"] == str(work)
        assert inputs[1]["input_id"] != inputs[0]["input_id"]
        assert [entry.args[1] for entry in receipts.await_args_list] == [
            "first",
            "steer",
        ]
        # Lose only the backend reader. The remote process completes on its own.
        await runtime._detach(session.topic_id)
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
        replacement.bind_events(consumer)
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
        await runtime._detach(session.topic_id)
        if replacement:
            await replacement._detach(session.topic_id)
        journal.close()
