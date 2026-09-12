"""Session placement survives storage while execution stays on the room machine."""

import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_provider import DeviceChannel, EnvironmentPreparationError
from app.domain.agent.harness.claude_code import ScreenSetupError
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.topic.models import Topic
from app.domain.topic.services import TopicService
from tests.integration.conftest import session_auth_headers


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
    ping = AsyncMock(return_value={"pid": 123})
    status = AsyncMock(return_value={"state": "ready"})
    monkeypatch.setattr("app.domain.agent.execution.call", ping)
    monkeypatch.setattr("app.domain.agent.central_provider.environment_status", status)
    await central.ensure_ready(
        project_id=project,
        topic_id=topic,
        token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
        env={"CHEESE_ENVIRONMENT": '{"revision":"one"}'} if has_environment else {},
        launch=ClaudeLaunch("System"),
        precheck=("executor", 1, "agent"),
    )
    assert ping.await_count == (0 if running else 1)
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
