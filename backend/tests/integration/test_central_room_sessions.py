"""Session placement survives storage while execution stays on the room machine."""

import asyncio
import json
import os
import subprocess
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import text

from app import device_connection_app
from app.core.config import settings
from app.core.db import get_db
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import execution, machine_launcher
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import Opening, SessionRef, deployment_harness
from app.domain.agent.harness.channel import Placement, ScreenSetupError
from app.domain.agent.harness.claude_code import ClaudeCodeRuntime
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.agent.harness.codex import CodexChannel
from app.domain.agent.harness.launch import MachinePlace
from app.domain.agent.harness.pi.device_launch import PiLaunch
from app.domain.agent_session.services import AgentSessionService
from app.domain.topic.models import Topic
from app.domain.topic.services import TopicService
from tests.integration.conftest import session_auth_headers
from tests.integration.test_archive_retires_storage import _seed_device
from tests.support import wire

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
    project = client.post(
        "/projects", json={"name": "Central", "owner_handle": "alice"}
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
    executor = DeviceChannel(hub=hub, session_factory=client.test_factory)
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
    with pytest.raises(ScreenSetupError, match="pi"):
        await central.ensure_ready(
            session=ref(project, topic),
            token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
            env={},
            launch=PiLaunch(system_prompt="System", model="glm-5.2"),
            precheck=await central.precheck(ref(project, topic), needs_place=True),
        )


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
    await selected.ensure_ready(
        session=ref(project, topic),
        token=mint_scoped_token(
            project_id=str(project), topic_id=str(topic), agent_handle="other-teammate"
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
            hook_url="http://fixture/hooks",
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
    assert result.stdout.strip() == "other-teammate <other-teammate@agent.cheese.local>"


@pytest.mark.anyio
async def test_codex_placement_recovers_only_as_codex(client, room, monkeypatch):
    project, topic = room
    central = channel(client, monkeypatch)
    central._hub.exec.side_effect = [
        {"exit": 0, "stdout": json.dumps({"thread_id": "codex-thread", "alive": True})},
    ]
    codex = CodexChannel(central, ClaudeLaunch("system").execution)
    session = SessionRef(project, topic, AGENT, harness="codex")
    actual_agent = (await central.precheck(session, needs_place=True)).agent_handle
    handle = await codex.ensure(
        session, Opening("shared system", model="fixture", agent_handle=actual_agent)
    )
    assert handle.thread_id == "codex-thread"
    place = await session_place(client.test_factory, topic, AGENT, "codex")
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
    # 中心通道同时被 Claude Code 和 Codex 两个 runtime 包着（``build_compute_pool``），
    # 所以它答不出哪一条会话是谁的，也不该答：它把落在自己这儿的会话原样交出来，
    # 骨架的名字当 ``running`` 一起交（``Channel.discover`` 的契约）。
    central.restore_screens = AsyncMock(
        side_effect=lambda scopes: [(p, t, None, None) for p, t, _ in scopes]
    )
    central.executor.discover = AsyncMock(return_value=[])
    assert await central.discover("center") == [(project, topic, None, "codex")]
    # 认领在 runtime 这一侧，判据是它自己的骨架——所以 Claude Code 一条也认不到，
    # 不靠平台层写一个 "claude-code" 把别人的会话挡在外面。
    assert await ClaudeCodeRuntime(central).recover("center") == []


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


@pytest.mark.anyio
async def test_room_starts_centrally_and_keeps_recorded_placement(
    client, room, monkeypatch
):
    project, topic = room
    central = channel(client, monkeypatch)
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
    place = await session_place(client.test_factory, topic)
    assert place is not None
    assert place.machine == "center"
    assert place.lease is None
    monkeypatch.setattr(settings, "agent_session_device_id", "another-host")
    assert await central.precheck(ref(project, topic), needs_place=True) == precheck
    central._hub.is_online = lambda device: device == "executor"
    with pytest.raises(ScreenSetupError, match="未连接"):
        await central.precheck(ref(project, topic), needs_place=True)


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
    client, room, monkeypatch, recorded, dialled
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
    async with client.test_factory() as db:
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
        async with client.test_factory() as db:
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
async def test_scoped_execution_and_rc_use_platform_owned_target(
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
        remote_control=True,
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
    created = client.post(
        "/v1/code/sessions",
        headers=headers,
        json={"execution": {"device_id": "attacker"}},
    )
    assert created.status_code == 200, created.text
    rc = created.json()["session"]
    assert rc["execution"] == {"resource_id": str(resource), "execution": target}
    result = client.post(
        f"/topics/{topic}/agent/control",
        headers=session_auth_headers("alice"),
        json={
            "session_id": rc["id"],
            "request": {"subtype": "read_file", "path": "draft.md"},
        },
    )
    assert result.status_code == 200, result.text
    assert call.await_args is not None
    assert call.await_args.args[0] == target
    async with client.test_factory() as db:
        stored = await TopicService(db).get_or_404(topic)
        stored.resource_id = uuid.uuid4()
        await db.commit()
    assert client.post(endpoint, headers=headers, json=payload).status_code == 409
    stale = client.post(
        f"/topics/{topic}/agent/control",
        headers=session_auth_headers("alice"),
        json={
            "session_id": rc["id"],
            "request": {"subtype": "read_file", "path": "draft.md"},
        },
    )
    assert stale.status_code == 409, stale.text
    stale_bootstrap = client.post("/v1/code/sessions", headers=headers, json={})
    assert stale_bootstrap.status_code == 409, stale_bootstrap.text
    async with client.test_factory() as db:
        stored = await db.get(Topic, topic)
        new_resource = stored.resource_id
        await place_session(db, topic, new_resource, target)
        await db.commit()
    # Changing the URL must not let the old credential reach the replacement.
    new_endpoint = f"/topics/{topic}/execution/{new_resource}"
    assert client.post(new_endpoint, headers=headers, json=payload).status_code == 409


@pytest.mark.anyio
async def test_scoped_execution_forwards_cli_tool_catalog(client, room, monkeypatch):
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
    catalog = {
        "tools": [
            {
                "name": "cheese_status",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]
    }
    call = AsyncMock(return_value=catalog)
    monkeypatch.setattr("app.domain.agent.execution.call", call)
    token = mint_scoped_token(
        project_id=str(project), topic_id=str(topic), resource_id=str(resource)
    )

    response = client.post(
        f"/topics/{topic}/execution/{resource}",
        headers={"X-Cheese-Token": token},
        json={"method": "cli", "params": {"method": "tools/list"}},
    )

    assert response.status_code == 200, response.text
    assert response.json() == catalog
    call.assert_awaited_once()
    assert call.await_args.args == (target, "cli", {"method": "tools/list"})
    assert call.await_args.kwargs["trace_id"].startswith("execution-")
