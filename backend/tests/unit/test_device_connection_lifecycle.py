"""A backend client can disappear without owning the executor call it started."""

import asyncio
import base64
import json
import uuid

import httpx
import pytest

from app import device_connection_app
from app.core.config import settings
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_hub_rpc import RemoteDeviceHub


class ExecutorTransport:
    sent: asyncio.Queue[dict]

    def __init__(self) -> None:
        self.sent = asyncio.Queue()

    async def send_json(self, message: dict) -> None:
        await self.sent.put(message)


@pytest.mark.anyio
async def test_executor_call_survives_backend_client_restart(monkeypatch) -> None:
    monkeypatch.setattr(settings, "device_connection_secret", "test-owner-secret")
    device_hub._devices.clear()
    device_hub._screens.clear()
    device_hub._by_screen_token.clear()
    device_connection_app._executor_calls.clear()

    connector = ExecutorTransport()
    await device_hub.attach_device("machine", connector)
    await connector.sent.get()  # welcome
    await device_hub.on_device_message(
        "machine", {"t": "hello", "v": 3, "executor": True}
    )

    transport = httpx.ASGITransport(app=device_connection_app.app)
    old_backend = RemoteDeviceHub(
        "http://owner", "test-owner-secret", transport=transport
    )
    await old_backend.start()
    first_waiter = asyncio.create_task(
        old_backend.call_executor(
            "machine",
            "/room/.claude/executor",
            "control",
            {"command": "sleep then answer"},
            trace_id="same-request-after-restart",
        )
    )
    outbound = await asyncio.wait_for(connector.sent.get(), 1)
    assert outbound["t"] == "execution.call"

    # This is the business backend being killed during a release. The owner and
    # its device call stay alive, and the replacement process starts with a new
    # client and an empty local cache.
    first_waiter.cancel()
    await asyncio.gather(first_waiter, return_exceptions=True)
    await old_backend.close()
    assert not device_connection_app._executor_calls[outbound["id"]].done()

    new_backend = RemoteDeviceHub(
        "http://owner", "test-owner-secret", transport=transport
    )
    await new_backend.start()
    recovered = asyncio.create_task(
        new_backend.call_executor(
            "machine",
            "/room/.claude/executor",
            "control",
            {"command": "sleep then answer"},
            trace_id="same-request-after-restart",
        )
    )
    encoded = json.dumps({"result": {"answer": "finished"}}).encode()
    await device_hub.on_device_message(
        "machine",
        {
            "t": "execution.data",
            "id": outbound["id"],
            "data": base64.b64encode(encoded).decode(),
        },
    )
    await device_hub.on_device_message(
        "machine",
        {"t": "execution.result", "id": outbound["id"], "error": ""},
    )

    assert await asyncio.wait_for(recovered, 1) == {"answer": "finished"}
    assert connector.sent.empty()  # retry reused the original device call
    assert new_backend.is_online("machine")
    await new_backend.close()
    await device_hub.detach_device("machine", connector)


@pytest.mark.anyio
async def test_new_backend_restores_screens_and_observes_later_connections(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "device_connection_secret", "test-owner-secret")
    device_hub._devices.clear()
    device_hub._screens.clear()
    device_hub._by_screen_token.clear()
    device_connection_app._executor_calls.clear()

    project_id = uuid.uuid4()
    topic_id = uuid.uuid4()
    screen = device_hub.adopt_screen(
        "machine",
        "screen-1",
        token="screen-token",
        agent_user_id=42,
        agent_handle="agent",
        project_id=project_id,
        topic_id=topic_id,
        resource_id=topic_id,
        hook_key="hook",
    )
    screen.execution_target = {"home": "/room"}

    transport = httpx.ASGITransport(app=device_connection_app.app)
    backend = RemoteDeviceHub("http://owner", "test-owner-secret", transport=transport)
    await backend.start()
    restored = await backend.adopt_screen(
        "machine",
        "screen-1",
        token="screen-token",
        agent_user_id=42,
        agent_handle="agent",
        project_id=project_id,
        topic_id=topic_id,
        resource_id=topic_id,
        hook_key="hook",
    )
    assert restored.project_id == project_id
    assert restored.topic_id == topic_id
    assert restored.execution_target == {"home": "/room"}

    recovered_devices: asyncio.Queue[str] = asyncio.Queue()

    async def recover(device_id: str) -> object:
        await recovered_devices.put(device_id)
        return 1

    backend.set_online_callback(recover)
    connector = ExecutorTransport()
    await device_hub.attach_device("machine", connector)
    await connector.sent.get()
    await backend.refresh()
    assert await asyncio.wait_for(recovered_devices.get(), 1) == "machine"
    assert backend.is_online("machine")

    await backend.close()
    await device_hub.detach_device("machine", connector)
