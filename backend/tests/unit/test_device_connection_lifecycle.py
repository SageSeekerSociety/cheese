"""A backend client can disappear without owning the executor call it started."""

import asyncio
import base64
import json
import uuid

import httpx
import pytest

from app import device_connection_app
from app.core.config import settings
from app.core.db import get_db
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_hub_rpc import RemoteDeviceHub


@pytest.fixture(autouse=True)
def connection_owner_role(monkeypatch) -> None:
    monkeypatch.setattr(settings, "device_connection_owner", True)


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
    device_connection_app._release_draining = False
    device_connection_app._active_rpc_calls = 0

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
    device_connection_app._release_draining = False
    device_connection_app._active_rpc_calls = 0

    project_id = uuid.uuid4()
    topic_id = uuid.uuid4()
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
        credential_expires=1234,
        agent_configuration="native",
        execution_target={"home": "/room"},
    )
    assert restored.project_id == project_id
    assert restored.topic_id == topic_id
    assert restored.execution_target == {"home": "/room"}
    owner_screen = device_hub.screen("screen-1")
    assert owner_screen is not None
    assert owner_screen.credential_expires == 1234
    assert owner_screen.agent_configuration == "native"

    await backend.update_screen(
        "screen-1",
        resource_id=topic_id,
        execution_target={"home": "/room", "revision": 2},
    )
    await backend.close()
    backend = RemoteDeviceHub("http://owner", "test-owner-secret", transport=transport)
    from app.domain.agent.harness.claude_code import (
        drop_device_subscriptions,
        drop_screen_subscriptions,
    )

    backend.set_subscription_cleanup_callbacks(
        drop_device=drop_device_subscriptions,
        drop_screen=drop_screen_subscriptions,
    )
    await backend.start()
    after_backend_restart = backend.screen("screen-1")
    assert after_backend_restart is not None
    assert after_backend_restart.execution_target == {
        "home": "/room",
        "revision": 2,
    }
    assert after_backend_restart.credential_expires == 1234

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

    # A reconnect wholly between two polls is still visible through the owner's
    # monotonically increasing generation and schedules business recovery again.
    await device_hub.detach_device("machine", connector)
    replacement = ExecutorTransport()
    await device_hub.attach_device("machine", replacement)
    await replacement.sent.get()
    await backend.refresh()
    assert await asyncio.wait_for(recovered_devices.get(), 1) == "machine"

    await backend.close()
    await device_hub.detach_device("machine", replacement)


@pytest.mark.anyio
async def test_backend_drops_subscriptions_from_owner_snapshot_changes(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "device_connection_secret", "test-owner-secret")
    device_hub._devices.clear()
    device_hub._screens.clear()
    device_hub._by_screen_token.clear()
    dropped_devices = []
    dropped_screens = []

    async def drop_device(device_id):
        dropped_devices.append(device_id)

    async def drop_screen(screen):
        dropped_screens.append(screen.sid)

    monkeypatch.setattr(
        "app.domain.agent.harness.claude_code.drop_device_subscriptions", drop_device
    )
    monkeypatch.setattr(
        "app.domain.agent.harness.claude_code.drop_screen_subscriptions", drop_screen
    )
    connector = ExecutorTransport()
    await device_hub.attach_device("machine", connector)
    await connector.sent.get()
    screen = device_hub.adopt_screen(
        "machine",
        "screen-1",
        token="token",
        agent_user_id=42,
        agent_handle="agent",
    )
    transport = httpx.ASGITransport(app=device_connection_app.app)
    backend = RemoteDeviceHub("http://owner", "test-owner-secret", transport=transport)
    backend.set_subscription_cleanup_callbacks(
        drop_device=drop_device, drop_screen=drop_screen
    )
    await backend.start()

    await device_hub.detach_device("machine", connector)
    await backend.refresh()
    assert dropped_devices == ["machine"]
    assert dropped_screens == ["screen-1"]

    device_hub._device("machine").screens.pop(screen.sid)
    device_hub._screens.pop(screen.sid)
    device_hub._by_screen_token.pop(screen.token)
    await backend.refresh()
    assert dropped_screens == ["screen-1", "screen-1"]
    await backend.close()


@pytest.mark.anyio
async def test_fast_reconnect_drops_the_real_old_subscription_before_recovery(
    monkeypatch,
) -> None:
    from app.domain.agent.device_provider import DeviceChannel
    from app.domain.agent.harness.claude_code import ClaudeCodeRuntime, HookRouter

    monkeypatch.setattr(settings, "device_connection_secret", "test-owner-secret")
    device_hub._devices.clear()
    device_hub._screens.clear()
    device_hub._by_screen_token.clear()
    first = ExecutorTransport()
    await device_hub.attach_device("machine", first)
    await first.sent.get()
    transport = httpx.ASGITransport(app=device_connection_app.app)
    backend = RemoteDeviceHub("http://owner", "test-owner-secret", transport=transport)
    from app.domain.agent.harness.claude_code import (
        drop_device_subscriptions,
        drop_screen_subscriptions,
    )

    backend.set_subscription_cleanup_callbacks(
        drop_device=drop_device_subscriptions,
        drop_screen=drop_screen_subscriptions,
    )
    await backend.start()

    project_id, topic_id = uuid.uuid4(), uuid.uuid4()
    router = HookRouter()
    channel = DeviceChannel(hub=backend)
    runtime = ClaudeCodeRuntime(channel, router=router)
    await runtime.ensure_subscription(project_id, topic_id, paused=True)
    channel._subscription_devices[topic_id] = "machine"
    assert router.push(str(topic_id), {"hook_event_name": "Stop"}) is True

    recovered_after_drop = asyncio.Event()

    async def recover(_: str) -> object:
        assert router.push(str(topic_id), {"hook_event_name": "Stop"}) is False
        recovered_after_drop.set()
        return 1

    backend.set_online_callback(recover)
    await device_hub.detach_device("machine", first)
    second = ExecutorTransport()
    await device_hub.attach_device("machine", second)
    await second.sent.get()
    await backend.refresh()
    await asyncio.wait_for(recovered_after_drop.wait(), 1)
    await backend.close()
    await device_hub.detach_device("machine", second)


@pytest.mark.anyio
async def test_release_drain_blocks_new_trace_but_keeps_completed_trace_readable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "device_connection_secret", "test-owner-secret")
    device_hub._devices.clear()
    device_connection_app._executor_calls.clear()
    device_connection_app._release_draining = False
    device_connection_app._active_rpc_calls = 0
    connector = ExecutorTransport()
    await device_hub.attach_device("machine", connector)
    await connector.sent.get()
    await device_hub.on_device_message(
        "machine", {"t": "hello", "v": 3, "executor": True}
    )
    transport = httpx.ASGITransport(app=device_connection_app.app)
    headers = {"X-Device-Connection-Secret": "test-owner-secret"}
    async with httpx.AsyncClient(
        base_url="http://owner", transport=transport, headers=headers
    ) as client:
        payload = {
            "device_id": "machine",
            "state": "/room/executor",
            "method": "ping",
            "params": {},
            "timeout": 2,
            "trace_id": "old-trace",
        }
        old_waiter = asyncio.create_task(
            client.post("/internal/device-connection/call/call_executor", json=payload)
        )
        outbound = await asyncio.wait_for(connector.sent.get(), 1)
        encoded = json.dumps({"result": {"pid": 1}}).encode()
        await device_hub.on_device_message(
            "machine",
            {
                "t": "execution.data",
                "id": outbound["id"],
                "data": base64.b64encode(encoded).decode(),
            },
        )
        await device_hub.on_device_message(
            "machine", {"t": "execution.result", "id": outbound["id"], "error": ""}
        )
        assert (await old_waiter).json() == {"result": {"pid": 1}}
        assert (
            await client.post("/internal/device-connection/release-drain")
        ).status_code == 200

        new_payload = {**payload, "trace_id": "new-trace"}
        blocked = await client.post(
            "/internal/device-connection/call/call_executor", json=new_payload
        )
        assert blocked.status_code == 503
        assert connector.sent.empty()
        readable = await client.post(
            "/internal/device-connection/call/call_executor", json=payload
        )
        assert readable.json() == {"result": {"pid": 1}}
        assert connector.sent.empty()

        assert (
            await client.post("/internal/device-connection/release-resume")
        ).status_code == 200
    await device_hub.detach_device("machine", connector)


@pytest.mark.anyio
async def test_release_drain_waits_for_exec_and_blocks_new_screen_call(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "device_connection_secret", "test-owner-secret")
    device_hub._devices.clear()
    device_connection_app._executor_calls.clear()
    device_connection_app._release_draining = False
    device_connection_app._active_rpc_calls = 0
    connector = ExecutorTransport()
    await device_hub.attach_device("machine", connector)
    await connector.sent.get()
    transport = httpx.ASGITransport(app=device_connection_app.app)
    headers = {"X-Device-Connection-Secret": "test-owner-secret"}
    async with httpx.AsyncClient(
        base_url="http://owner", transport=transport, headers=headers
    ) as client:
        exec_waiter = asyncio.create_task(
            client.post(
                "/internal/device-connection/call/exec",
                json={"device_id": "machine", "argv": ["sleep", "1"]},
            )
        )
        outbound = await asyncio.wait_for(connector.sent.get(), 1)
        assert outbound["t"] == "exec"
        active = await client.post("/internal/device-connection/release-drain")
        assert active.status_code == 409
        status = (await client.get("/internal/device-connection/snapshot")).json()
        assert status["active_rpc_calls"] == 1
        assert status["device_pending"]["machine"]["exec"] == 1

        await device_hub.on_device_message(
            "machine",
            {
                "t": "exec.result",
                "id": outbound["id"],
                "exitCode": 0,
                "stdout": "done",
                "stderr": "",
            },
        )
        assert (await exec_waiter).status_code == 200
        assert (
            await client.post("/internal/device-connection/release-drain")
        ).status_code == 200

        blocked = await client.post(
            "/internal/device-connection/call/call_screen",
            json={
                "device_id": "machine",
                "sid": "screen-1",
                "name": "read",
                "args": [],
            },
        )
        assert blocked.status_code == 503
        assert connector.sent.empty()
        await client.post("/internal/device-connection/release-resume")
    await device_hub.detach_device("machine", connector)


@pytest.mark.anyio
async def test_public_execution_admission_blocks_drain_during_route_preparation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "device_connection_secret", "test-owner-secret")
    device_connection_app._release_draining = False
    device_connection_app._active_rpc_calls = 0
    entered = asyncio.Event()
    release = asyncio.Event()
    db_calls = 0

    async def blocked_db():
        nonlocal db_calls
        db_calls += 1
        entered.set()
        await release.wait()
        yield None

    device_connection_app.app.dependency_overrides[get_db] = blocked_db
    transport = httpx.ASGITransport(app=device_connection_app.app)
    try:
        async with httpx.AsyncClient(
            base_url="http://owner", transport=transport
        ) as client:
            request = asyncio.create_task(
                client.post(
                    f"/topics/{uuid.uuid4()}/execution/{uuid.uuid4()}",
                    json={"method": "ping", "params": {}},
                )
            )
            await asyncio.wait_for(entered.wait(), 1)
            drain = await client.post(
                "/internal/device-connection/release-drain",
                headers={"X-Device-Connection-Secret": "test-owner-secret"},
            )
            assert drain.status_code == 409
            assert device_connection_app._active_rpc_calls == 1
            release.set()
            assert (await request).status_code == 401

            device_connection_app._release_draining = True
            calls_before_blocked = db_calls
            blocked = await client.post(
                f"/topics/{uuid.uuid4()}/execution/{uuid.uuid4()}",
                json={"method": "ping", "params": {}},
            )
            assert blocked.status_code == 503
            assert db_calls == calls_before_blocked
            assert device_connection_app._active_rpc_calls == 0
    finally:
        release.set()
        device_connection_app.app.dependency_overrides.pop(get_db, None)
        device_connection_app._release_draining = False
