"""Session placement survives storage while execution stays on the room machine."""

import asyncio
import base64
import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app import device_connection_app
from app.core.config import settings
from app.core.db import get_db
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_provider import DeviceChannel, EnvironmentPreparationError
from app.domain.agent.harness import Opening, SessionRef
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent.harness.claude_code.remote_execution import (
    client as execution_client,
)
from app.domain.agent.harness.claude_code.remote_execution import (
    runtime as executor_runtime,
)
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.agent.harness.codex import CodexChannel
from app.domain.agent.harness.pi.device_launch import PiLaunch
from app.domain.topic.models import Topic
from app.domain.topic.services import TopicService
from tests.integration.conftest import session_auth_headers


class _OwnerExecutorTransport:
    def __init__(self) -> None:
        self.sent: asyncio.Queue[dict] = asyncio.Queue()

    async def send_json(self, msg: dict[str, Any]) -> None:
        await self.sent.put(msg)


@pytest.fixture
def room(client):
    project = client.post(
        "/projects", json={"name": "Central", "owner_handle": "alice"}
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work", "created_by": "alice"},
    ).json()["data"]
    return uuid.UUID(project["id"]), uuid.UUID(topic["id"])


def channel(client, monkeypatch):
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    hub: Any = SimpleNamespace(
        is_online=lambda device: device in {"center", "executor"},
        call_executor=AsyncMock(return_value={"generation": "fixture", "entries": {}}),
        exec=AsyncMock(
            return_value={
                "exit": 0,
                "stdout": json.dumps({"workspace": "/project", "mcp_servers": []}),
            }
        ),
    )
    executor = DeviceChannel(hub=hub, session_factory=client.test_factory)
    executor.precheck = AsyncMock(return_value=("executor", 1, "agent"))
    executor._device_api_base = AsyncMock(return_value="http://execution-api")
    central: Any = CentralChannel(executor)
    central._device_api_base = AsyncMock(return_value="http://central-api")
    central._ensure_screen = AsyncMock(return_value=SimpleNamespace(device_id="center"))
    central._wait_executor = AsyncMock()
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
            project_id=project,
            topic_id=topic,
            token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
            env={},
            launch=PiLaunch(system_prompt="System", model="glm-5.2"),
            precheck=await central.precheck(project, topic),
        )


@pytest.mark.anyio
async def test_codex_placement_recovers_only_as_codex(client, room, monkeypatch):
    project, topic = room
    central = channel(client, monkeypatch)
    central._hub.exec.side_effect = [
        {"exit": 0, "stdout": json.dumps({"workspace": "/project", "mcp_servers": []})},
        {"exit": 0, "stdout": json.dumps({"thread_id": "codex-thread", "alive": True})},
    ]
    codex = CodexChannel(central, ClaudeLaunch("system").execution)
    ref = SessionRef(project, topic)
    handle = await codex.ensure(
        ref, Opening("shared system", model="fixture", agent_handle="agent")
    )
    assert handle.thread_id == "codex-thread"
    async with client.test_factory() as db:
        stored = await db.get(Topic, topic)
        assert stored.session_placement["runtime"] == {
            "harness": "codex",
            "agent_handle": "agent",
            "state": handle.state,
        }
        assert stored.session_placement["execution"]["device_id"] == "executor"
    central._hub.call_executor.return_value = {
        "thread_id": "codex-thread",
        "alive": True,
    }
    assert await codex.discover("center") == [handle]
    central.restore_screens = AsyncMock(return_value=[])
    central.executor.discover = AsyncMock(return_value=[])
    assert await central.discover("center") == []
    central.restore_screens.assert_awaited_once_with([])


@pytest.mark.anyio
async def test_center_uses_the_selected_harness_for_bootstrap_and_history(
    client, room, monkeypatch
):
    project, topic = room
    central = channel(client, monkeypatch)
    history = AsyncMock()
    launch = SimpleNamespace(
        harness="claude-code",
        system_prompt="System",
        model=None,
        on=lambda place: None,
        at=lambda place: None,
        resume_session_id="fixture-session",
        execution=SimpleNamespace(
            transfer_history=history,
            script=lambda *args: "FIXTURE_EXECUTOR_BOOTSTRAP",
            payload_for=lambda *args: {"fixture_executor": True},
            can_prepare=lambda info: "prepare" in info.get("capabilities", []),
        ),
    )
    kwargs = dict(
        project_id=project,
        topic_id=topic,
        token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
        env={},
        launch=launch,
        precheck=await central.precheck(project, topic),
    )
    await central.ensure_ready(**kwargs)
    assert central._hub.exec.await_args.kwargs["stdin"] == "FIXTURE_EXECUTOR_BOOTSTRAP"
    history.assert_awaited_once_with(
        central._hub, "executor", "center", project, topic, "fixture-session"
    )
    central._hub.call_executor.side_effect = [
        {"pid": 123, "capabilities": ["prepare"]},
        {
            "pid": 123,
            "workspace": "/project",
            "mcp_servers": [],
            "context_tree": {"generation": "fixture", "entries": {}},
        },
    ]
    await central.ensure_ready(**kwargs)
    assert central._hub.call_executor.await_args.args[3] == {"fixture_executor": True}
    assert history.await_count == 1


@pytest.mark.anyio
async def test_stopped_previous_executor_http_failure_takes_installation_path(
    client, room, monkeypatch
):
    project, topic = room
    central = channel(client, monkeypatch)
    kwargs = dict(
        project_id=project,
        topic_id=topic,
        token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
        env={},
        launch=ClaudeLaunch("System"),
        precheck=await central.precheck(project, topic),
    )
    await central.ensure_ready(**kwargs)
    central._hub.exec.reset_mock()
    request = httpx.Request("POST", "http://owner/call_executor")
    central._hub.call_executor.side_effect = [
        httpx.HTTPStatusError(
            "executor socket is not ready",
            request=request,
            response=httpx.Response(500, request=request),
        ),
        {"generation": "fixture", "entries": {}},
    ]

    await central.ensure_ready(**kwargs)

    central._hub.exec.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("digest", [None, "previous-release"])
async def test_old_executor_process_takes_release_bootstrap(
    client, room, monkeypatch, digest
):
    project, topic = room
    central = channel(client, monkeypatch)
    kwargs = dict(
        project_id=project,
        topic_id=topic,
        token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
        env={},
        launch=ClaudeLaunch("System"),
        precheck=await central.precheck(project, topic),
    )
    await central.ensure_ready(**kwargs)
    central._hub.exec.reset_mock()
    central._hub.call_executor.return_value = {
        "pid": 123,
        "capabilities": ["prepare"],
        "runtime_sha256": digest,
    }
    async with client.test_factory() as admitted:
        await execution.lock_release(admitted, topic, shared=True)
        update = asyncio.create_task(central.ensure_ready(**kwargs))
        try:
            await asyncio.sleep(0.2)
            central._hub.exec.assert_not_awaited()
        finally:
            await admitted.rollback()
            await asyncio.wait_for(update, 10)
    central._hub.exec.assert_awaited_once()
    assert [
        call.args[2] for call in central._hub.call_executor.await_args_list[-2:]
    ] == ["ping", "context_fs"]


@pytest.mark.anyio
@pytest.mark.parametrize("environment_state", ["ready", "pending", "failed"])
async def test_running_executor_prepares_without_python_launch(
    client, room, monkeypatch, environment_state
):
    project, topic = room
    central = channel(client, monkeypatch)
    kwargs = dict(
        project_id=project,
        topic_id=topic,
        token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
        env={"CHEESE_ENVIRONMENT": '{"revision":"one"}'},
        launch=ClaudeLaunch("System"),
        precheck=await central.precheck(project, topic),
    )
    await central.ensure_ready(**kwargs)
    central._hub.exec.reset_mock()
    central._wait_executor.reset_mock()
    central._hub.call_executor.reset_mock()
    central._hub.call_executor.side_effect = [
        {
            "pid": 123,
            "capabilities": ["prepare"],
            "runtime_sha256": executor_runtime.SOURCE_SHA256,
        },
        {
            "pid": 123,
            "workspace": "/project",
            "mcp_servers": [],
            "environment_status": environment_state,
            "context_tree": {"generation": "fixture", "entries": {}},
        },
    ]
    await central.ensure_ready(**kwargs)
    central._hub.exec.assert_not_awaited()
    calls = central._hub.call_executor.await_args_list
    assert [call.args[2] for call in calls] == ["ping", "prepare"]
    payload = calls[1].args[3]
    assert payload["env"]["CHEESE_TOKEN"]
    assert payload["environment"] == {"revision": "one"}
    assert "cheese-hook" in payload["files"]
    if environment_state == "ready":
        central._wait_executor.assert_not_awaited()
    else:
        central._wait_executor.assert_awaited_once()


@pytest.mark.anyio
async def test_running_executor_prepare_failure_is_not_retried_as_install(
    client, room, monkeypatch
):
    project, topic = room
    central = channel(client, monkeypatch)
    kwargs = dict(
        project_id=project,
        topic_id=topic,
        token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
        env={},
        launch=ClaudeLaunch("System"),
        precheck=await central.precheck(project, topic),
    )
    await central.ensure_ready(**kwargs)
    central._hub.exec.reset_mock()
    central._hub.call_executor.side_effect = [
        {
            "pid": 123,
            "capabilities": ["prepare"],
            "runtime_sha256": executor_runtime.SOURCE_SHA256,
        },
        RuntimeError("Executor configuration changed"),
    ]
    with pytest.raises(RuntimeError, match="configuration changed"):
        await central.ensure_ready(**kwargs)
    central._hub.exec.assert_not_awaited()


@pytest.mark.anyio
async def test_room_starts_centrally_and_keeps_recorded_placement(
    client, room, monkeypatch
):
    project, topic = room
    central = channel(client, monkeypatch)
    precheck = await central.precheck(project, topic)
    screen = await central.ensure_ready(
        project_id=project,
        topic_id=topic,
        token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
        env={"CHEESE_ENVIRONMENT": '{"revision":"one"}'},
        launch=ClaudeLaunch("System"),
        precheck=precheck,
        turn_id=uuid.uuid4(),
    )
    assert screen.device_id == "center"
    assert central._hub.exec.await_args.args[0] == "executor"
    opening = central._ensure_screen.await_args.kwargs
    assert "CHEESE_ENVIRONMENT" not in opening["env"]
    target = json.loads(opening["env"]["CHEESE_EXECUTION_TARGET"])
    assert target["device_id"] == "executor"
    assert target["context_tree"] == {"generation": "fixture", "entries": {}}
    assert target["url"].startswith("http://central-api/")
    async with client.test_factory() as db:
        stored = await db.get(Topic, topic)
        assert stored.session_placement["device_id"] == "center"
        assert stored.session_placement["execution"] == target
    monkeypatch.setattr(settings, "agent_session_device_id", "another-host")
    assert await central.precheck(project, topic) == precheck
    central._hub.is_online = lambda device: device == "executor"
    with pytest.raises(ScreenSetupError, match="未连接"):
        await central.precheck(project, topic)


@pytest.mark.anyio
async def test_execution_drift_fails_before_touching_either_machine(
    client, room, monkeypatch
):
    project, topic = room
    central = channel(client, monkeypatch)
    async with client.test_factory() as db:
        stored = await db.get(Topic, topic)
        stored.session_placement = {
            "device_id": "center",
            "resource_id": str(stored.resource_id or topic),
            "channel": "device",
            "execution": {"device_id": "original"},
        }
        await db.commit()
    with pytest.raises(ScreenSetupError, match="不一致"):
        await central.ensure_ready(
            project_id=project,
            topic_id=topic,
            token="scoped",
            env={},
            launch=ClaudeLaunch("System"),
            precheck=("executor", 1, "agent"),
        )
    central._hub.exec.assert_not_called()
    central._ensure_screen.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("has_environment", [False, True])
async def test_executor_readiness_reuses_bootstrap_reply(
    client, room, monkeypatch, running, has_environment
):
    project, topic = room
    central = channel(client, monkeypatch)
    reply = {"workspace": "/project", "mcp_servers": []}
    if running:
        reply["pid"] = 123
        reply["environment_status"] = "ready"
    central._hub.exec.return_value["stdout"] = json.dumps(reply)
    central._wait_executor = CentralChannel._wait_executor.__get__(central)
    call = AsyncMock(
        side_effect=lambda target, method, params, **kwargs: (
            {"pid": 123}
            if method == "ping"
            else {"generation": "fixture", "entries": {}}
        )
    )
    status = AsyncMock(return_value={"state": "ready"})
    monkeypatch.setattr("app.domain.agent.execution.call", call)
    monkeypatch.setattr("app.domain.agent.central_provider.environment_status", status)
    await central.ensure_ready(
        project_id=project,
        topic_id=topic,
        token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
        env={"CHEESE_ENVIRONMENT": '{"revision":"one"}'} if has_environment else {},
        launch=ClaudeLaunch("System"),
        precheck=("executor", 1, "agent"),
    )
    assert [item.args[1] for item in call.await_args_list] == (
        ["context_fs"] if running else ["ping", "context_fs"]
    )
    assert status.await_count == (1 if has_environment and not running else 0)
    central._ensure_screen.assert_awaited_once()


@pytest.mark.anyio
async def test_running_executor_does_not_hide_failed_environment(
    client, room, monkeypatch
):
    project, topic = room
    central = channel(client, monkeypatch)
    central._hub.exec.return_value["stdout"] = json.dumps(
        {
            "workspace": "/project",
            "mcp_servers": [],
            "pid": 123,
            "environment_status": "failed",
        }
    )
    central._wait_executor = CentralChannel._wait_executor.__get__(central)
    status = AsyncMock(return_value={"state": "failed", "error": "build failed"})
    ping = AsyncMock()
    monkeypatch.setattr("app.domain.agent.central_provider.environment_status", status)
    monkeypatch.setattr("app.domain.agent.execution.call", ping)
    with pytest.raises(EnvironmentPreparationError):
        await central.ensure_ready(
            project_id=project,
            topic_id=topic,
            token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
            env={"CHEESE_ENVIRONMENT": '{"revision":"one"}'},
            launch=ClaudeLaunch("System"),
            precheck=("executor", 1, "agent"),
        )
    status.assert_awaited_once()
    ping.assert_not_awaited()
    central._ensure_screen.assert_not_awaited()


@pytest.mark.anyio
async def test_executor_release_excludes_new_tool_admission(client, room, monkeypatch):
    project, topic = room
    async with client.test_factory() as db:
        stored = await db.get(Topic, topic)
        resource = stored.resource_id or topic
        stored.session_placement = {
            "device_id": "center",
            "resource_id": str(resource),
            "channel": "device",
            "execution": {"kind": "device", "device_id": "executor"},
        }
        await db.commit()
    entered = threading.Event()

    async def invoke(*args, **kwargs):
        entered.set()
        return {"value": "kept"}

    monkeypatch.setattr(execution, "call", invoke)
    token = mint_scoped_token(
        project_id=str(project), topic_id=str(topic), resource_id=str(resource)
    )
    async with client.test_factory() as release:
        await execution.lock_release(release, resource)
        request = asyncio.create_task(
            asyncio.to_thread(
                client.post,
                f"/topics/{topic}/execution/{resource}",
                headers={"X-Cheese-Token": token},
                json={
                    "method": "invoke",
                    "params": {"tool": "Read", "args": {"file_path": "draft.md"}},
                },
            )
        )
        try:
            assert not await asyncio.to_thread(entered.wait, 0.2)
        finally:
            await release.rollback()
            response = await asyncio.wait_for(request, 5)
    assert response.status_code == 200, response.text
    assert entered.is_set()
    assert response.json() == {"value": "kept"}


@pytest.mark.anyio
async def test_owner_execution_route_preserves_scope_and_reaches_device(
    client, room, monkeypatch
):
    project, topic = room
    async with client.test_factory() as db:
        stored = await db.get(Topic, topic)
        resource = stored.resource_id or topic
        stored.session_placement = {
            "device_id": "center",
            "resource_id": str(resource),
            "channel": "device",
            "execution": {
                "kind": "device",
                "device_id": "executor",
                "home": "/room",
            },
        }
        await db.commit()

    async def owner_db():
        async with client.test_factory() as db:
            yield db

    monkeypatch.setattr(settings, "device_connection_owner", True)
    device_hub._devices.clear()
    device_connection_app._release_draining = False
    device_connection_app._active_rpc_calls = 0
    connector = _OwnerExecutorTransport()
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
            outbound = await asyncio.wait_for(connector.sent.get(), 1)
            assert outbound["t"] == "execution.call"
            assert outbound["path"] == "/room/.cheese/executor"
            encoded = json.dumps({"result": {"content": "executor file"}}).encode()
            await device_hub.on_device_message(
                "executor",
                {
                    "t": "execution.data",
                    "id": outbound["id"],
                    "data": base64.b64encode(encoded).decode(),
                },
            )
            await device_hub.on_device_message(
                "executor",
                {"t": "execution.result", "id": outbound["id"], "error": ""},
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
        placement = {
            "device_id": "center",
            "resource_id": str(resource),
            "channel": "device",
            "execution": target,
        }
        stored.session_placement = placement
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
    assert rc["execution"] == placement
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
        stored.session_placement = {**placement, "resource_id": str(new_resource)}
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
        stored.session_placement = {
            "device_id": "center",
            "resource_id": str(resource),
            "channel": "device",
            "execution": target,
        }
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


@pytest.mark.anyio
async def test_native_tool_catalog_crosses_scoped_execution_route(
    client, room, monkeypatch, tmp_path
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
        stored.session_placement = {
            "device_id": "center",
            "resource_id": str(resource),
            "channel": "device",
            "execution": target,
        }
        await db.commit()
    token = mint_scoped_token(
        project_id=str(project), topic_id=str(topic), resource_id=str(resource)
    )

    async def call(_target, method, params, **_kwargs):
        assert _target == target
        if method == "ping":
            return {"capabilities": ["cli_worker"]}
        assert method == "cli"
        assert params == {"method": "tools/list"}
        return {
            "tools": [
                {
                    "name": "cheese_status",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ]
        }

    monkeypatch.setattr("app.domain.agent.execution.call", call)
    endpoint = f"/topics/{topic}/execution/{resource}"

    class RouteBridge(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            response = client.post(
                endpoint, headers={"X-Cheese-Token": token}, json=payload
            )
            body = response.content
            self.send_response(response.status_code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), RouteBridge)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    config = tmp_path / "execution.json"
    config.write_text(
        json.dumps(
            {
                "kind": "device",
                "url": f"http://127.0.0.1:{server.server_port}{endpoint}",
                "workspace": str(tmp_path),
                "central_hooks": {},
            }
        )
    )
    log = (tmp_path / "native-mcp.log").open("w+")
    process = executor_runtime.MCPProcess(
        [sys.executable, execution_client.__file__, "transport", str(config)],
        str(tmp_path),
        {**os.environ, "NO_PROXY": "127.0.0.1", "CHEESE_TOKEN": token},
        log,
    )
    try:
        tools = {tool["name"] for tool in process.call("tools/list", {})["tools"]}
        assert {"invoke", "chat_send", "platform_request", "cheese_status"} <= tools
    finally:
        process.close()
        server.shutdown()
        server.server_close()
        thread.join()
        log.close()
