"""Session placement survives storage while execution stays on the room machine."""

import asyncio
import contextlib
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import text

from app import device_connection_app
from app.api.deps import get_chat_service
from app.core.config import settings
from app.core.db import get_db
from app.core.sandbox_auth import mint_scoped_token, scoped_token_claims
from app.domain.agent import execution, machine_launcher
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import Opening, SessionRef, deployment_harness
from app.domain.agent.harness.channel import Placement, ScreenSetupError
from app.domain.agent.harness.claude_code import ClaudeCodeChannel, ClaudeCodeRuntime
from app.domain.agent.harness.claude_code.bundle import build
from app.domain.agent.harness.claude_code.cli import LAUNCH_ARGS
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.agent.harness.codex import CodexChannel
from app.domain.agent.harness.driven.runner import socket_path
from app.domain.agent.harness.launch import MachinePlace
from app.domain.agent.harness.pi.device_launch import PiLaunch
from app.domain.agent_session.services import AgentSessionService
from app.domain.topic.models import Topic
from app.domain.topic.services import TopicService
from app.main import app as fastapi_app
from tests.integration.conftest import post_project, session_auth_headers
from tests.integration.test_archive_retires_storage import _seed_device
from tests.pinned_claude import claude_binary
from tests.support import wire
from tests.unit.test_device_provider import FakeHub

AGENT = "agent"


def ref(project, topic, agent=AGENT):
    """A session key, which is what a place is recorded under."""
    return SessionRef(project, topic, agent, harness="claude-code")


async def place_session(db, topic, resource, target, *, agent=AGENT, machine="center"):
    """Put one session of this room on a machine, hands and process both."""
    await AgentSessionService(db).remember_place(
        topic_id=topic,
        agent_handle=agent,
        work_lease=target,
        runtime_location={
            "device_id": machine,
            "resource_id": str(resource),
            "channel": "device",
        },
        harness=deployment_harness(),
    )


async def session_place(factory, topic, agent=AGENT, harness="claude-code"):
    async with factory() as db:
        return await AgentSessionService(db).place(topic, agent, harness=harness)


@pytest.mark.anyio
async def test_execution_survives_an_unrelated_room_column_rename(
    client, room, monkeypatch
):
    project, topic = room
    async with client.test_factory() as db:
        stored = await db.get(Topic, topic)
        resource = stored.resource_id or topic
        await place_session(
            db, topic, resource, {"kind": "device", "device_id": "executor"}
        )
        await db.commit()
        await db.execute(
            text("ALTER TABLE topics RENAME COLUMN title TO retired_title")
        )
        await db.commit()
        try:
            call = AsyncMock(return_value={"content": "still running"})
            monkeypatch.setattr(execution, "call", call)
            token = mint_scoped_token(
                project_id=str(project), topic_id=str(topic), resource_id=str(resource)
            )
            response = client.post(
                f"/topics/{topic}/execution/{resource}",
                headers={"X-Cheese-Token": token},
                json={"method": "ping"},
            )
            assert response.status_code == 200, response.text
            assert response.json() == {"content": "still running"}
        finally:
            await db.execute(
                text("ALTER TABLE topics RENAME COLUMN retired_title TO title")
            )
            await db.commit()


@pytest.fixture
async def room(client):
    project = post_project(
        client, json={"name": "Central", "owner_handle": "alice"}
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work", "created_by": "alice"},
    ).json()["data"]
    made = client.post(f"/projects/{project['id']}/agents", json={"handle": AGENT})
    assert made.status_code == 200, made.text
    project_id = uuid.UUID(project["id"])
    async with client.test_factory() as db:
        await _seed_device(db, "executor", project_id=project_id)
        await db.commit()
    return project_id, uuid.UUID(topic["id"])


def channel(client, monkeypatch):
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    hub: Any = SimpleNamespace(
        is_online=lambda device: device in {"center", "executor"},
        call_executor=AsyncMock(return_value={"generation": "fixture", "entries": {}}),
        exec=AsyncMock(
            return_value={
                "exit": 0,
                "stdout": json.dumps(INSTALLED),
            }
        ),
    )
    executor = DeviceChannel(hub=hub, session_factory=client.test_request_factory)
    executor.precheck = AsyncMock(
        return_value=Placement("executor", 1, "agent", rented=True)
    )
    executor._device_api_base = AsyncMock(return_value="http://execution-api")
    central: Any = CentralChannel(executor)
    central._device_api_base = AsyncMock(return_value="http://central-api")
    central._ensure_screen = AsyncMock(return_value=SimpleNamespace(device_id="center"))
    return central


@pytest.mark.anyio
async def test_a_harness_without_an_executor_is_refused_by_name(
    client, room, monkeypatch
):
    """这条路要另指派一台执行机，所以计划得会装执行器、会把对话搬过去。

    A harness that runs where the files already are answers neither, and the
    room has to be told which one it was rather than watch a screen fail to
    open. Codex hands this same route a plan with ONLY those two answers and
    no screen at all, so the check cannot be spelled as "a whole launch plan"
    and cannot live where Codex passes through.
    """
    project, topic = room
    central = channel(client, monkeypatch)

    async def exercise():
        with pytest.raises(ScreenSetupError, match="pi"):
            await central.ensure_ready(
                session=ref(project, topic),
                token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
                env={},
                launch=PiLaunch(system_prompt="System", model="glm-5.2"),
                precheck=await central.precheck(ref(project, topic), needs_place=True),
            )

    client.portal.call(exercise)


# What the executor installation reports about itself. ``state`` is where it put
# the executor: the placement carries that answer rather than deriving it, so
# the process that reaches the executor later — released separately from the one
# that installs it — cannot disagree about it. See `agent.execution`.
INSTALLED = {
    "workspace": "/project",
    "mcp_servers": [],
    "state": "/room/.cheese/executor",
}


@pytest.mark.anyio
@pytest.mark.parametrize("central_execution", [False])
async def test_commits_use_the_authenticated_teammate_not_the_room_identity(
    client, room, monkeypatch, tmp_path, central_execution
):
    from app.domain.agent.harness.claude_code.remote_execution import launch
    from app.domain.identity.services import IdentityService

    project, topic = room
    async with client.test_factory() as db:
        actor = await IdentityService(db).ensure_agent_user(handle="other-teammate")
        actor_id = actor.id
        await db.commit()
    central = channel(client, monkeypatch)
    captured = {}
    central._hub.all_online_screens = lambda: []

    def bootstrap(project, resource, env):
        captured.update(env)
        return "fixture-bootstrap"

    monkeypatch.setattr(launch, "script", bootstrap)
    selected = central if central_execution else central.executor
    selected._ensure_screen = AsyncMock(
        return_value=SimpleNamespace(device_id="center")
    )

    async def exercise():
        await selected.ensure_ready(
            session=ref(project, topic),
            token=mint_scoped_token(
                project_id=str(project),
                topic_id=str(topic),
                agent_handle="other-teammate",
            ),
            env={},
            memory_scope=None,
            owner=None,
            turn_id=None,
            launch=ClaudeLaunch("System"),
            precheck=Placement("executor", 1, "room-stand-in", rented=True),
        )
        screen = selected._ensure_screen.await_args.kwargs
        assert screen["agent_handle"] == "other-teammate"
        assert screen["agent_user_id"] == actor_id
        if not central_execution:
            captured = machine_launcher.screen_env(
                MachinePlace(
                    home=str(tmp_path),
                    workdir=str(tmp_path),
                    store="",
                    state="",
                    api_base="http://fixture",
                    project_id=str(project),
                    topic_id=str(topic),
                    agent_handle=screen["agent_handle"],
                ),
                token=screen["token"],
            )
        assert captured["CHEESE_AUTHOR"] == "other-teammate"
        env = {
            **os.environ,
            **captured,
            "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.test",
        }
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path),
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--allow-empty",
                "-m",
                "Fixture",
            ],
            env=env,
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            ["git", "-C", str(tmp_path), "show", "-s", "--format=%an <%ae>"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert (
            result.stdout.strip()
            == "other-teammate <other-teammate@agent.cheese.local>"
        )

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_codex_placement_recovers_only_as_codex(client, room, monkeypatch):
    project, topic = room
    central = channel(client, monkeypatch)

    async def exercise():
        central._hub.exec.side_effect = [
            {
                "exit": 0,
                "stdout": json.dumps({"thread_id": "codex-thread", "alive": True}),
            },
        ]
        codex = CodexChannel(central, ClaudeLaunch("system").execution)
        session = SessionRef(project, topic, AGENT, harness="codex")
        actual_agent = (await central.precheck(session, needs_place=True)).agent_handle
        handle = await codex.ensure(
            session,
            Opening("shared system", model="fixture", agent_handle=actual_agent),
        )
        assert handle.thread_id == "codex-thread"
        place = await session_place(client.test_request_factory, topic, AGENT, "codex")
        assert place is not None
        assert place.runtime == {
            "harness": "codex",
            "agent_handle": actual_agent,
            "state": handle.state,
        }
        assert place.lease is None
        central._hub.call_executor.return_value = {
            "thread_id": "codex-thread",
            "alive": True,
        }
        assert await codex.discover("center") == [handle]
        # 中心通道同时被几个骨架的 runtime 包着（``build_compute_pool``），
        # 所以它答不出哪一条会话是谁的，也不该答：它只把落在自己这儿的屏认回来。
        central.restore_screens = AsyncMock()
        await central.restore("center")
        central.restore_screens.assert_awaited_once_with([(project, topic, "center")])
        # 认领在 runtime 这一侧，判据是它自己的骨架——所以 Claude Code 一条也认不到，
        # 不靠平台层写一个 "claude-code" 把别人的会话挡在外面。
        claude = ClaudeCodeRuntime(ClaudeCodeChannel(central))
        assert await claude.recover("center") == []

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_a_room_stays_writable_while_its_agent_is_starting(
    client, room, monkeypatch
):
    """Starting an agent holds nothing on the room's row.

    Installing the executor and opening the screen are remote work measured in
    minutes. While they run, people are renaming the room, archiving it, marking
    it read — and every one of those writers used to queue behind the setup's
    row lock, each holding a database connection of its own for as long as it
    waited. That is how one slow room start became every request in the process
    waiting for a connection that never came back.

    `FOR UPDATE NOWAIT` from a second connection is the whole question: it
    raises if anything holds the row, and returns if nothing does.
    """
    project, topic = room
    central = channel(client, monkeypatch)

    async def exercise():

        async def free_while(gate: asyncio.Event) -> None:
            await asyncio.wait_for(gate.wait(), 5)
            async with client.test_factory() as writer:
                held = await writer.execute(
                    text("SELECT id FROM topics WHERE id = :id FOR UPDATE NOWAIT"),
                    {"id": topic},
                )
                assert held.scalar_one() == topic
                await writer.rollback()

        opening = asyncio.Event()
        opened = asyncio.Event()

        async def slow_screen(*args, **kwargs):
            opening.set()
            await opened.wait()
            return SimpleNamespace(device_id="center")

        central._ensure_screen = AsyncMock(side_effect=slow_screen)

        setup = asyncio.create_task(
            central.ensure_ready(
                session=ref(project, topic),
                token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
                env={},
                launch=ClaudeLaunch("System"),
                precheck=await central.precheck(ref(project, topic), needs_place=True),
            )
        )
        try:
            await free_while(opening)
            opened.set()
            await asyncio.wait_for(setup, 10)
        finally:
            opened.set()
            setup.cancel()

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_room_starts_centrally_and_keeps_recorded_placement(
    client, room, monkeypatch
):
    project, topic = room
    central = channel(client, monkeypatch)

    async def exercise():
        precheck = await central.precheck(ref(project, topic), needs_place=True)
        screen = await central.ensure_ready(
            session=ref(project, topic),
            token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
            env={"CHEESE_ENVIRONMENT": '{"revision":"one"}'},
            launch=ClaudeLaunch("System"),
            precheck=precheck,
            turn_id=uuid.uuid4(),
        )
        assert screen.device_id == "center"
        central._hub.exec.assert_not_awaited()
        opening = central._ensure_screen.await_args.kwargs
        assert "CHEESE_ENVIRONMENT" not in opening["env"]
        target = json.loads(opening["env"]["CHEESE_EXECUTION_TARGET"])
        assert target["kind"] == "deferred"
        assert target["lease_path"].endswith("/work-lease")
        place = await session_place(client.test_request_factory, topic)
        assert place is not None
        assert place.machine == "center"
        assert place.lease is None
        monkeypatch.setattr(settings, "agent_session_device_id", "another-host")
        assert await central.precheck(ref(project, topic), needs_place=True) == precheck
        central._hub.is_online = lambda device: device == "executor"
        with pytest.raises(ScreenSetupError, match="未连接"):
            await central.precheck(ref(project, topic), needs_place=True)

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_scoped_execution_does_not_hold_admission_connection_during_remote_call(
    client, room, monkeypatch
):
    """A long tool call must not pin the request's database connection.

    Admission is enforced by the device connection owner while the remote call
    is in flight.  The business route only needs the database for the initial
    scope check; retaining a transaction-scoped advisory lock here consumes one
    pool slot for every active tool and makes ordinary page requests time out.
    """
    project, topic = room
    async with client.test_factory() as db:
        stored = await db.get(Topic, topic)
        resource = stored.resource_id or topic
        await place_session(
            db, topic, resource, {"kind": "device", "device_id": "executor"}
        )
        await db.commit()

    entered = asyncio.Event()
    finish = asyncio.Event()

    async def invoke(*args, **kwargs):
        entered.set()
        await finish.wait()
        return {"value": "kept"}

    monkeypatch.setattr(execution, "call", invoke)
    token = mint_scoped_token(
        project_id=str(project), topic_id=str(topic), resource_id=str(resource)
    )
    request = asyncio.create_task(
        asyncio.to_thread(
            client.post,
            f"/topics/{topic}/execution/{resource}",
            headers={"X-Cheese-Token": token},
            json={"method": "invoke", "params": {}},
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    finish.set()
    response = await asyncio.wait_for(request, 5)

    assert response.status_code == 200, response.text
    assert response.json() == {"value": "kept"}


@pytest.mark.parametrize(
    ("recorded", "dialled"),
    [
        pytest.param({}, "/room/.cheese/executor", id="recorded-before-the-field"),
        pytest.param(
            {"state": "/room/.elsewhere/executor"},
            "/room/.elsewhere/executor",
            id="recorded-by-the-installation",
        ),
    ],
)
@pytest.mark.anyio
async def test_owner_execution_route_preserves_scope_and_reaches_device(
    db_factory, room, monkeypatch, recorded, dialled
):
    """The owner dials where the placement says, not where it would install.

    This route is served by the device connection owner, which an app deploy
    deliberately leaves alone, so it routinely runs an older build than the
    backend that placed the room. A directory this process derives is therefore
    a directory two builds can disagree about, and when they did — the install
    root moved in one of them — every room's tools and every bootstrap ping
    failed against a path that was correct in the other half.
    """
    project, topic = room
    async with db_factory() as db:
        stored = await db.get(Topic, topic)
        resource = stored.resource_id or topic
        await place_session(
            db,
            topic,
            resource,
            {
                "kind": "device",
                "device_id": "executor",
                "home": "/room",
                **recorded,
            },
        )
        await db.commit()

    async def owner_db():
        async with db_factory() as db:
            yield db

    monkeypatch.setattr(settings, "device_connection_owner", True)
    device_hub._devices.clear()
    device_connection_app._release_draining = False
    device_connection_app._active_rpc_calls = 0
    connector = wire.RecordingDevice()
    await device_hub.attach_device("executor", connector)
    await connector.sent.get()
    await device_hub.on_device_message(
        "executor", {"t": "hello", "v": 3, "executor": True}
    )
    device_connection_app.app.dependency_overrides[get_db] = owner_db
    transport = httpx.ASGITransport(app=device_connection_app.app)
    token = mint_scoped_token(
        project_id=str(project), topic_id=str(topic), resource_id=str(resource)
    )
    endpoint = f"/topics/{topic}/execution/{resource}"
    payload = {"method": "invoke", "params": {"tool": "Read", "args": {}}}
    try:
        async with httpx.AsyncClient(
            base_url="http://owner", transport=transport
        ) as owner:
            waiter = asyncio.create_task(
                owner.post(endpoint, headers={"X-Cheese-Token": token}, json=payload)
            )
            call = await connector.next_call()
            assert call.path == dialled
            encoded = json.dumps({"result": {"content": "executor file"}}).encode()
            await device_hub.on_device_message(
                "executor", wire.execution_data(call.id, encoded)
            )
            await device_hub.on_device_message(
                "executor", wire.execution_result(call.id)
            )
            response = await asyncio.wait_for(waiter, 2)
            assert response.status_code == 200, response.text
            assert response.json() == {"content": "executor file"}
            assert (await owner.post(endpoint, json=payload)).status_code == 401
            assert (
                await owner.post(
                    endpoint,
                    headers={"X-Cheese-Token": token},
                    json={"method": "configure"},
                )
            ).status_code == 403
            assert (
                await owner.post(
                    f"/topics/{topic}/execution/{uuid.uuid4()}",
                    headers={"X-Cheese-Token": token},
                    json=payload,
                )
            ).status_code == 409
            assert connector.sent.empty()
    finally:
        device_connection_app.app.dependency_overrides.pop(get_db, None)
        await device_hub.detach_device("executor", connector)


@pytest.mark.anyio
async def test_a_machine_that_does_not_answer_is_not_a_fault_of_this_server(
    client, room, monkeypatch
):
    """A device that holds its link and stays silent answered 500「服务器内部
    错误」, which blames the one process it cannot be — and, an unhandled error
    being logged three times on its way out, said so three times into the alert
    channel. The connection owner's own RPC path has answered 504 for this
    since it was written."""
    project, topic = room
    async with client.test_factory() as db:
        stored = await db.get(Topic, topic)
        resource = stored.resource_id or topic
        await place_session(
            db, topic, resource, {"kind": "device", "device_id": "executor"}
        )
        await db.commit()

    async def never_answers(*_args, **_kwargs):
        raise TimeoutError

    monkeypatch.setattr(execution, "call", never_answers)
    token = mint_scoped_token(
        project_id=str(project), topic_id=str(topic), resource_id=str(resource)
    )
    response = await asyncio.to_thread(
        client.post,
        f"/topics/{topic}/execution/{resource}",
        headers={"X-Cheese-Token": token},
        json={"method": "invoke", "params": {"tool": "Read"}},
    )
    assert response.status_code == 504, response.text


@pytest.mark.anyio
async def test_a_teammate_that_rented_no_hands_is_not_an_executor(
    client, room, monkeypatch
):
    """手就在会话机上的那条会话没有租约，它不该把执行路由拖成 500。

    pi 每一轮都把 `work_lease` 写成「没有」。只要「没租到手」落库时成了 JSON 的
    `null` 而不是 SQL NULL，`work_lease IS NOT NULL` 这条谓词就在说谎：那一行被
    选进候选，读的人拿到 None，一个本该干净的 409 变成 500。一个房间里同时坐着
    pi 和 claude-code 两条会话时——换骨架，或者两个队友各跑各的骨架——就会撞上。
    """
    project, topic = room
    async with client.test_factory() as db:
        stored = await db.get(Topic, topic)
        resource = stored.resource_id or topic
        await AgentSessionService(db).remember_place(
            topic_id=topic,
            agent_handle="pi-teammate",
            harness="pi",
            work_lease=None,
            runtime_location={
                "device_id": "center",
                "resource_id": str(resource),
                "channel": "device",
            },
        )
        await db.commit()
    token = mint_scoped_token(
        project_id=str(project), topic_id=str(topic), resource_id=str(resource)
    )
    endpoint = f"/topics/{topic}/execution/{resource}"
    payload = {"method": "invoke", "params": {"tool": "Read"}}
    headers = {"X-Cheese-Token": token}

    # 房间里只有那条没有租约的会话：这一代没有手可借。
    assert client.post(endpoint, headers=headers, json=payload).status_code == 409

    # 旁边坐下一个真租了手的队友，借的就是它那一份。
    target = {"kind": "device", "device_id": "executor", "resource_id": str(resource)}
    async with client.test_factory() as db:
        await place_session(db, topic, resource, target)
        await db.commit()
    call = AsyncMock(return_value={"content": "executor file"})
    monkeypatch.setattr("app.domain.agent.execution.call", call)

    response = client.post(endpoint, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert call.await_args.args[0] == target


@pytest.mark.anyio
async def test_scoped_execution_and_controls_use_platform_owned_target(
    client, room, monkeypatch
):
    project, topic = room
    async with client.test_factory() as db:
        stored = await db.get(Topic, topic)
        resource = stored.resource_id or topic
        target = {
            "kind": "device",
            "device_id": "executor",
            "resource_id": str(resource),
        }
        await place_session(db, topic, resource, target)
        await db.commit()
    call = AsyncMock(return_value={"content": "executor file"})
    monkeypatch.setattr("app.domain.agent.execution.call", call)
    endpoint = f"/topics/{topic}/execution/{resource}"
    token = mint_scoped_token(
        project_id=str(project),
        topic_id=str(topic),
        resource_id=str(resource),
    )
    headers = {"X-Cheese-Token": token}
    payload = {
        "method": "invoke",
        "params": {"tool": "Read", "args": {"file_path": "draft.md"}},
    }
    response = client.post(endpoint, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json() == {"content": "executor file"}
    assert call.await_args is not None
    assert call.await_args.args == (target, "invoke", payload["params"])
    assert call.await_args.kwargs["trace_id"].startswith("execution-")
    context_fs = {"method": "context_fs", "params": {"operation": "tree"}}
    response = client.post(endpoint, headers=headers, json=context_fs)
    assert response.status_code == 200, response.text
    assert call.await_args.args == (target, "context_fs", context_fs["params"])
    assert call.await_args.kwargs["trace_id"].startswith("execution-")
    assert client.post(endpoint, json=payload).status_code == 401
    assert (
        client.post(endpoint, headers=headers, json={"method": "configure"}).status_code
        == 403
    )
    assert (
        client.post(
            f"/topics/{topic}/execution/{uuid.uuid4()}", headers=headers, json=payload
        ).status_code
        == 409
    )
    wrong = mint_scoped_token(project_id=str(project), topic_id=str(uuid.uuid4()))
    assert (
        client.post(
            endpoint, headers={"X-Cheese-Token": wrong}, json=payload
        ).status_code
        == 401
    )

    # A room's live session, as the controls see it: reading a file is the
    # executor's to answer, so it goes to the lease the platform recorded.
    async def control_state(_topic):
        return {"id": "session-1", "tasks": {}}

    session = SimpleNamespace(
        controls=("read_file",),
        executor_controls=frozenset({"read_file"}),
        control_state=control_state,
    )
    fastapi_app.dependency_overrides[get_chat_service] = lambda: SimpleNamespace(
        session_controls=lambda _topic: session
    )
    request = {
        "session_id": "session-1",
        "request": {"subtype": "read_file", "path": "draft.md"},
    }
    try:
        result = client.post(
            f"/topics/{topic}/agent/control",
            headers=session_auth_headers("alice"),
            json=request,
        )
        assert result.status_code == 200, result.text
        assert call.await_args.args == (target, "control", request["request"])
        async with client.test_factory() as db:
            stored = await TopicService(db).get_or_404(topic)
            stored.resource_id = uuid.uuid4()
            await db.commit()
        assert client.post(endpoint, headers=headers, json=payload).status_code == 409
        # The lease belongs to the generation the room has left behind.
        stale = client.post(
            f"/topics/{topic}/agent/control",
            headers=session_auth_headers("alice"),
            json=request,
        )
        assert stale.status_code == 409, stale.text
    finally:
        fastapi_app.dependency_overrides.pop(get_chat_service, None)
    async with client.test_factory() as db:
        stored = await db.get(Topic, topic)
        new_resource = stored.resource_id
        await place_session(db, topic, new_resource, target)
        await db.commit()
    # Changing the URL must not let the old credential reach the replacement.
    new_endpoint = f"/topics/{topic}/execution/{new_resource}"
    assert client.post(new_endpoint, headers=headers, json=payload).status_code == 409


class CenterHub(FakeHub):
    """The session host, a connector whose screens run a Claude Code runner,
    and the room's machine, connected while ``machine_up``."""

    def __init__(self) -> None:
        super().__init__()
        self.machine_up = True
        self.machine_asked = 0

    def is_online(self, device_id: str) -> bool:
        return device_id == "center" or (device_id == "executor" and self.machine_up)

    async def call_executor(self, device_id, state, method, params, **options):
        if device_id != "executor":
            return await super().call_executor(device_id, state, method, params)
        self.machine_asked += 1
        if not self.machine_up:
            raise TimeoutError("the machine does not answer")
        return {"workspace": "/home/machine", "mcp_servers": []}


def center_room(client, monkeypatch):
    """A room's central sessions opened on ``CenterHub``, and a call that
    starts the room's next turn there, offering its conversation to resume."""
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    hub = CenterHub()
    executor = DeviceChannel(hub=hub, session_factory=client.test_request_factory)
    central: Any = CentralChannel(executor)
    central._device_api_base = AsyncMock(return_value="http://central-api")

    async def turn(project, topic):
        session = ref(project, topic)
        return await central.ensure_ready(
            session=session,
            token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
            env={},
            launch=ClaudeLaunch("System", resume_session_id="conversation"),
            precheck=await central.precheck(session, needs_place=True),
        )

    return hub, turn


async def lease_machine(factory, topic, workspace):
    """The session's first project tool rented its machine; the lease is ready."""
    async with factory() as db:
        row = await AgentSessionService(db).ensure(
            topic, AGENT, harness=deployment_harness()
        )
        generation = str(uuid.uuid4())
        row.work_lease = {
            "kind": "device",
            "device_id": "executor",
            "generation": generation,
            "status": "ready",
            "workspace": workspace,
            "url": "http://central-api/execution",
            "state": "/home/machine/.cheese/executor",
        }
        await db.commit()
    return generation


def launched(hub, screen):
    """The execution target and the credential a screen was started with."""
    # Every open gets the next sid (`FakeHub`), and its environment in order.
    env = hub.envs[int(screen.sid.removeprefix("s")) - 1] or {}
    target = json.loads(env["CHEESE_EXECUTION_TARGET"])
    return target, scoped_token_claims(env["CHEESE_TOKEN"]), env


@pytest.mark.anyio
async def test_a_session_started_before_its_machine_moves_there_once_idle(
    client, room, monkeypatch
):
    """A session started before its room's machine was rented sees the project
    at a placeholder. Once the machine is leased, the first turn that finds the
    session idle relaunches it where the machine holds the project, resuming the
    same conversation; a turn or a task still running holds the relaunch off,
    and it happens once."""
    project, topic = room
    hub, turn = center_room(client, monkeypatch)

    async def exercise():
        first = await turn(project, topic)
        target, claims, _ = launched(hub, first)
        assert target["workspace"] == "/unavailable-project"
        generation = await lease_machine(
            client.test_request_factory, topic, "/home/machine/the project"
        )

        for busy in (
            {"working": True, "tasks": {}},
            {"working": False, "tasks": {"b1": "local_bash"}},
        ):
            hub.ping = {"alive": True, **busy}
            assert await turn(project, topic) is first
        assert hub.closed == []

        hub.ping = {"alive": True, "working": False, "tasks": {}}
        moved = await turn(project, topic)
        assert moved.sid != first.sid and hub.closed == [first.sid]
        target, claims, env = launched(hub, moved)
        assert target["workspace"] == "/home/machine/the project"
        assert env["CHEESE_RESUME_SESSION"] == "conversation"
        assert claims["lease"] == generation
        assert await turn(project, topic) is moved
        assert hub.closed == [first.sid]

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_a_session_started_on_its_leased_machine_stays(client, room, monkeypatch):
    project, topic = room
    hub, turn = center_room(client, monkeypatch)

    async def exercise():
        await turn(project, topic)
        await lease_machine(client.test_request_factory, topic, "/home/machine/p")
        hub.ping = {"alive": True, "working": False, "tasks": {}}
        started = await turn(project, topic)
        assert launched(hub, started)[0]["workspace"] == "/home/machine/p"
        closed = list(hub.closed)

        for _ in range(3):
            assert await turn(project, topic) is started
        assert hub.closed == closed

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_a_relaunch_waits_for_the_machine_to_answer(client, room, monkeypatch):
    """A session is never started at its machine's path without the machine. A
    turn that finds the placeholder session idle while its leased machine is
    out of reach runs on it as it is, and the relaunch waits, as it waits for a
    running task, for the first idle turn that finds the machine answering."""
    project, topic = room
    hub, turn = center_room(client, monkeypatch)

    async def exercise():
        first = await turn(project, topic)
        await lease_machine(client.test_request_factory, topic, "/home/machine/p")
        hub.ping = {"alive": True, "working": False, "tasks": {}}

        hub.machine_up = False
        for _ in range(2):
            assert await turn(project, topic) is first
        assert hub.closed == []

        hub.machine_up = True
        moved = await turn(project, topic)
        assert moved.sid != first.sid and hub.closed == [first.sid]
        assert launched(hub, moved)[0]["workspace"] == "/home/machine/p"

        # On the machine, it stays there while the machine is out of reach.
        hub.machine_up = False
        asked = hub.machine_asked
        assert await turn(project, topic) is moved
        assert hub.machine_asked == asked and hub.closed == [first.sid]

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_a_session_started_while_its_machine_is_away_starts_at_the_placeholder(
    client, room, monkeypatch
):
    """A session that has to start again — its process is gone — while its
    leased machine is out of reach starts where it was, at the placeholder,
    holding its lease for its first tool."""
    project, topic = room
    hub, turn = center_room(client, monkeypatch)

    async def exercise():
        first = await turn(project, topic)
        await lease_machine(client.test_request_factory, topic, "/home/machine/p")
        hub.machine_up = False
        hub.ping = {"alive": False}
        started = await turn(project, topic)
        assert started.sid != first.sid
        target, claims, _ = launched(hub, started)
        assert target["workspace"] == "/unavailable-project"
        assert claims["lease"]

    client.portal.call(exercise)


class SaysDone(BaseHTTPRequestHandler):
    """A Messages API that ends every turn by saying "done"."""

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "/count_tokens" in self.path:
            return self._send("application/json", {"input_tokens": 10})
        message = {
            "id": "msg_fixture",
            "type": "message",
            "role": "assistant",
            "model": body.get("model"),
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        if not body.get("stream"):
            message.update(
                content=[{"type": "text", "text": "done"}], stop_reason="end_turn"
            )
            return self._send("application/json", message)
        text = {"type": "text_delta", "text": "done"}
        events = [
            ("message_start", {"type": "message_start", "message": message}),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            (
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": text},
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 5},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        stream = "".join(
            f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events
        )
        self._send("text/event-stream", stream)

    def _send(self, content_type, payload):
        encoded = (
            payload if isinstance(payload, str) else json.dumps(payload)
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@pytest.mark.anyio
async def test_a_room_session_is_the_runner_its_screen_started(
    client, room, monkeypatch, tmp_path
):
    """The room reaches its session through the runner, and finds it again.

    The runner archive and the pinned build are the real ones; only the model
    and the machine's connector are stand-ins. The connector's part is small:
    expand the recorded state directory on the machine and relay one line to
    the socket the runner derives from it. So what the channel recorded is
    what a later process dials — at the start, and after a restart.
    """
    project, topic = room
    model = ThreadingHTTPServer(("127.0.0.1", 0), SaysDone)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    home = tmp_path / "host"
    (home / ".claude").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    archive = tmp_path / "runner.pyz"
    archive.write_bytes(build())
    runners: list[subprocess.Popen] = []

    def on_machine(state: str) -> Path:
        return Path(state.replace("$HOME", str(home)))

    async def call_executor(device_id, state, method, params, timeout=None):
        assert device_id == "center"
        reader, writer = await asyncio.open_unix_connection(
            socket_path(on_machine(state))
        )
        try:
            writer.write(json.dumps({"method": method, "params": params}).encode())
            writer.write(b"\n")
            await writer.drain()
            answer = json.loads(await reader.readline())
        finally:
            writer.close()
        if "error" in answer:
            raise RuntimeError(answer["error"])
        return answer["result"]

    async def open_screen(**kwargs):
        place = await session_place(client.test_request_factory, topic)
        command = [claude_binary(), "--model", "claude-sonnet-4-5", *LAUNCH_ARGS]
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "CLAUDE_CONFIG_DIR": str(home / ".claude"),
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{model.server_address[1]}",
            "ANTHROPIC_API_KEY": "fixture-not-a-real-key",
            "NO_PROXY": "127.0.0.1,localhost",
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CHEESE_AUTHOR": kwargs["agent_handle"],
            "CHEESE_CLAUDE_COMMAND": shlex.join(command),
        }
        state = on_machine(place.runtime["state"])
        runners.append(
            subprocess.Popen(
                [sys.executable, str(archive), "--state", str(state)],
                cwd=work,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=(tmp_path / "runner.log").open("ab"),
                start_new_session=True,
            )
        )
        return SimpleNamespace(device_id="center")

    central = channel(client, monkeypatch)
    central._hub.call_executor = call_executor
    central._ensure_screen = open_screen
    claude = ClaudeCodeChannel(central)

    async def exercise():
        session = ref(project, topic)
        handle = await claude.ensure(session, Opening("System"))
        place = await session_place(client.test_request_factory, topic)
        assert place.runtime["state"] == handle.state
        assert handle.session_id

        sent = {"input_id": "message-1", "text": "hello", "work_id": str(uuid.uuid4())}
        assert await claude.call(handle, "send", sent) == {"input_id": "message-1"}
        async with asyncio.timeout(60):
            while True:
                records = [
                    entry["record"]
                    for entry in (await claude.call(handle, "events", {}))["events"]
                ]
                if any(record.get("type") == "result" for record in records):
                    break
                await asyncio.sleep(0.2)
        result = next(record for record in records if record.get("type") == "result")
        assert result["is_error"] is False, result
        assert result["session_id"] == handle.session_id

        # A backend that restarted knows the session only by its row.
        central.restore_screens = AsyncMock()
        assert await ClaudeCodeChannel(central).discover("center") == [handle]

    try:
        client.portal.call(exercise)
    finally:
        for runner in runners:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(runner.pid, signal.SIGTERM)
            runner.wait(timeout=30)
        model.shutdown()
        model.server_close()
