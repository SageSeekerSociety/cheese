"""DeviceHub: the server end of the link.Msg protocol (I/O-free, fake transports)."""

import asyncio
import base64
import hashlib
import json
import uuid

import pytest

from app.domain.agent import connector_build
from app.domain.agent.device_hub import DeviceHub, DeviceOffline


class FakeDeviceTransport:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)


class FakeViewer:
    def __init__(self) -> None:
        self.bytes_: list[bytes] = []

    async def send_bytes(self, data: bytes) -> None:
        self.bytes_.append(data)


async def test_close_waits_for_confirmation_and_retries_after_disconnect():
    hub = DeviceHub()
    transport = FakeDeviceTransport()
    await hub.attach_device("dev", transport)
    screen = await hub.open_screen("dev", ["sleep", "60"], **_screen_args())
    await hub.detach_device("dev", transport)
    with pytest.raises(DeviceOffline):
        await hub.close_screen("dev", screen.sid)
    assert hub.screen(screen.sid) is screen
    assert screen.closing
    await hub.attach_device("dev", transport)
    closing = asyncio.create_task(hub.close_screen("dev", screen.sid))
    await asyncio.sleep(0)
    assert not closing.done()
    assert hub.screen(screen.sid) is screen
    request = transport.sent[-1]
    await hub.on_device_message(
        "dev", {"t": "session.result", "id": request["id"], "error": "kill failed"}
    )
    with pytest.raises(RuntimeError, match="kill failed"):
        await closing
    assert hub.screen(screen.sid) is screen
    closing = asyncio.create_task(hub.close_screen("dev", screen.sid))
    await asyncio.sleep(0)
    await hub.on_device_message(
        "dev", {"t": "session.result", "id": transport.sent[-1]["id"]}
    )
    assert await closing is True
    assert hub.screen(screen.sid) is None


@pytest.mark.parametrize("owner", [False, True])
async def test_local_hub_only_runs_subscription_cleanup_for_business_role(
    monkeypatch, owner
):
    from unittest.mock import AsyncMock

    from app.core.config import settings

    drop_device = AsyncMock()
    drop_screen = AsyncMock()
    monkeypatch.setattr(settings, "device_connection_owner", owner)
    monkeypatch.setattr(
        "app.domain.agent.harness.claude_code.drop_device_subscriptions", drop_device
    )
    monkeypatch.setattr(
        "app.domain.agent.harness.claude_code.drop_screen_subscriptions", drop_screen
    )
    hub = DeviceHub()
    transport = FakeDeviceTransport()
    await hub.attach_device("dev", transport)
    screen = hub.adopt_screen("dev", "screen", token="token", **_screen_args())

    await hub.detach_device("dev", transport)

    assert drop_device.await_count == (0 if owner else 1)
    assert drop_screen.await_count == (0 if owner else 1)

    await hub.attach_device("dev", transport)
    closing = asyncio.create_task(hub.close_screen("dev", screen.sid))
    await asyncio.sleep(0)
    await hub.on_device_message(
        "dev", {"t": "session.result", "id": transport.sent[-1]["id"]}
    )
    assert await closing is True
    assert drop_screen.await_count == (0 if owner else 2)


async def test_inventory_accepts_a_reply_during_send():
    hub = DeviceHub()

    class ImmediateTransport:
        async def send_json(self, msg):
            if msg["t"] == "session.list":
                await hub.on_device_message(
                    "dev",
                    {
                        "t": "session.result",
                        "id": msg["id"],
                        "value": [{"sid": "survivor"}],
                    },
                )

    await hub.attach_device("dev", ImmediateTransport())
    assert await hub.list_screens("dev") == [{"sid": "survivor"}]


def test_reconnect_reads_inventory_replies_while_recovery_is_running(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import connector
    from app.core.db import get_db

    hub = DeviceHub()
    recovered = []

    class Chat:
        async def recover_sessions(self, device_id):
            inventory = await hub.session_request(
                device_id, {"t": "session.list"}, timeout=1
            )
            recovered.extend(inventory)

    class Wakeup:
        async def wake_device(self, device_id):
            await hub._device(device_id).send({"t": "recovery.finished"})

    monkeypatch.setattr(connector, "device_hub", hub)
    monkeypatch.setattr("app.api.deps.get_chat_service", lambda: Chat())
    monkeypatch.setattr("app.api.deps.get_cloud_wakeup", lambda: Wakeup())
    monkeypatch.setattr("app.domain.topic.retire.sweep_retired_storage", AsyncMock())
    app = FastAPI()
    app.include_router(connector.router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(commit=AsyncMock())
    service = SimpleNamespace(
        verify_token=AsyncMock(
            return_value=SimpleNamespace(device_id="dev", name="fixture")
        )
    )
    app.dependency_overrides[connector.get_device_service] = lambda: service
    with (
        TestClient(app) as client,
        client.websocket_connect("/connector/agent?token=fixture") as ws,
    ):
        assert ws.receive_json()["t"] == "welcome"
        request = ws.receive_json()
        assert request["t"] == "session.list"
        ws.send_json(
            {"t": "session.result", "id": request["id"], "value": [{"sid": "survivor"}]}
        )
        assert ws.receive_json()["t"] == "recovery.finished"
    assert recovered == [{"sid": "survivor"}]


def test_connection_owner_attaches_without_running_business_recovery(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import connector
    from app.core.db import get_db

    hub = DeviceHub()
    monkeypatch.setattr(connector, "device_hub", hub)
    monkeypatch.setattr(connector.settings, "device_connection_owner", True)
    monkeypatch.setattr(
        "app.api.deps.get_chat_service",
        lambda: (_ for _ in ()).throw(AssertionError("owner ran ChatService")),
    )
    app = FastAPI()
    app.include_router(connector.router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(commit=AsyncMock())
    service = SimpleNamespace(
        verify_token=AsyncMock(
            return_value=SimpleNamespace(device_id="dev", name="fixture")
        )
    )
    app.dependency_overrides[connector.get_device_service] = lambda: service

    with (
        TestClient(app) as client,
        client.websocket_connect("/connector/agent?token=fixture") as ws,
    ):
        assert ws.receive_json()["t"] == "welcome"
        assert hub.is_online("dev")


def _screen_args() -> dict:
    return {
        "agent_user_id": uuid.uuid4(),
        "agent_handle": "agent-x",
        "project_id": uuid.uuid4(),
        "topic_id": uuid.uuid4(),
    }


async def test_attach_sends_welcome():
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    assert t.sent == [{"t": "welcome", "v": 1}]
    assert hub.is_online("dev1")


async def _executor_call():
    hub = DeviceHub()
    transport = FakeDeviceTransport()
    await hub.attach_device("dev1", transport)
    await hub.on_device_message("dev1", {"t": "hello", "executor": True})
    task = asyncio.create_task(hub.call_executor("dev1", "/room/state", "ping", {}))
    await asyncio.sleep(0)
    return hub, transport, task, transport.sent[-1]["id"]


async def test_executor_does_not_wait_for_an_unsupported_connector():
    hub = DeviceHub()
    transport = FakeDeviceTransport()
    await hub.attach_device("dev1", transport)
    with pytest.raises(RuntimeError, match="updating"):
        await hub.call_executor("dev1", "/room/state", "ping", {})
    assert len(transport.sent) == 1


async def test_executor_result_waits_for_complete_response(caplog):
    caplog.set_level("INFO", logger="app.domain.agent.device_hub")
    hub, transport, task, identifier = await _executor_call()
    data = json.dumps({"result": {"text": "中文"}}, ensure_ascii=False).encode()
    for chunk in (data[:23], data[23:]):
        await hub.on_device_message(
            "dev1",
            {
                "t": "execution.data",
                "id": identifier,
                "data": base64.b64encode(chunk).decode(),
            },
        )
    assert not task.done()
    await hub.on_device_message("dev1", {"t": "execution.result", "id": identifier})
    assert await task == {"text": "中文"}
    messages = [r.message for r in caplog.records if "execution_timing" in r.message]
    assert [m.split("stage=", 1)[1].split()[0] for m in messages] == [
        "device_send_start",
        "device_sent",
        "device_first_data",
        "device_complete",
    ]
    assert all(f"trace={identifier}" in m for m in messages)
    assert all("中文" not in m for m in messages)


async def test_executor_disconnect_reports_unknown_outcome_without_replay():
    hub, transport, task, _ = await _executor_call()
    await hub.detach_device("dev1", transport)
    with pytest.raises(DeviceOffline):
        await task
    assert len([m for m in transport.sent if m["t"] == "execution.call"]) == 1


async def test_executor_cancellation_reaches_connector():
    _, transport, task, identifier = await _executor_call()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.sent[-1] == {"t": "exec.cancel", "id": identifier}


async def test_executor_interrupted_response_is_not_returned_as_success():
    hub, _, task, identifier = await _executor_call()
    await hub.on_device_message(
        "dev1",
        {
            "t": "execution.result",
            "id": identifier,
            "error": "executor disconnected",
        },
    )
    with pytest.raises(RuntimeError, match="disconnected"):
        await task


async def test_open_screen_sends_session_create_and_registers_token():
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    screen = await hub.open_screen("dev1", ["claude"], env={"K": "V"}, **_screen_args())
    create = t.sent[-1]
    assert create["t"] == "session.create"
    assert create["command"] == ["claude"]
    assert create["env"] == {"K": "V"} and create["screen"] == screen.token
    # The screen is discoverable by its token (attribution) and by sid.
    assert hub.screen_by_token(screen.token) is screen
    assert hub.screen(screen.sid) is screen


async def test_exec_on_an_offline_device_fails_at_once_instead_of_timing_out():
    hub = DeviceHub()
    with pytest.raises(DeviceOffline):
        await hub.exec("never-attached", ["true"], timeout=1)
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    await hub.detach_device("dev1", t)
    with pytest.raises(DeviceOffline):
        await hub.exec("dev1", ["true"], timeout=1)
    assert t.sent == [{"t": "welcome", "v": 1}], "nothing was sent into the void"


async def test_the_hub_can_name_a_machine_and_date_its_last_frame():
    hub = DeviceHub()
    assert hub.device_name("dev1") == "dev1", "unknown machines answer to their id"
    assert hub.last_seen_age("dev1") is None
    await hub.attach_device("dev1", FakeDeviceTransport(), name="andy 的笔记本")
    assert hub.device_name("dev1") == "andy 的笔记本"
    assert hub.last_seen_age("dev1") is None, "attached, but it has not spoken yet"
    await hub.on_device_message("dev1", {"t": "heartbeat"})
    age = hub.last_seen_age("dev1")
    assert age is not None and 0 <= age < 1


async def test_hello_version_skew_is_flagged_not_fatal():
    seen: list[int | None] = []

    class Hub(DeviceHub):
        def _on_version_skew(self, device_id: str, proto: int | None) -> None:
            seen.append(proto)

    hub = Hub()
    await hub.attach_device("dev1", FakeDeviceTransport())
    await hub.on_device_message("dev1", {"t": "hello", "v": 999})
    assert seen == [999]  # flagged, connection stays up


async def test_screen_data_fans_out_to_viewers_only():
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    screen = await hub.open_screen("dev1", ["claude"], **_screen_args())
    viewer = FakeViewer()
    await hub.attach_viewer("dev1", screen.sid, viewer)
    assert t.sent[-1]["t"] == "screen.subscribe"  # first viewer subscribes

    await hub.on_device_message(
        "dev1",
        {
            "t": "screen.data",
            "sid": screen.sid,
            "data": base64.b64encode(b"px").decode(),
        },
    )
    assert viewer.bytes_ == [b"px"]


async def test_call_screen_await_resolved_by_rpc_result():
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    screen = await hub.open_screen("dev1", ["claude"], **_screen_args())
    call_id = await hub.call_screen("dev1", screen.sid, "prompt", ["hi"])
    assert t.sent[-1] == {
        "t": "rpc.call",
        "sid": screen.sid,
        "id": call_id,
        "name": "prompt",
        "args": ["hi"],
    }
    # The device answers → await_call resolves with the value.
    import asyncio

    fut = asyncio.ensure_future(hub.await_call("dev1", call_id, timeout=2))
    await asyncio.sleep(0)
    await hub.on_device_message(
        "dev1",
        {"t": "rpc.result", "sid": screen.sid, "id": call_id, "value": {"ok": True}},
    )
    assert await fut == {"ok": True}


async def test_put_file_waits_for_device_file_result():
    hub = DeviceHub()
    transport = FakeDeviceTransport()
    await hub.attach_device("dev1", transport)
    screen = await hub.open_screen("dev1", ["claude"], **_screen_args())

    import asyncio

    pending = asyncio.create_task(
        hub.put_file("dev1", screen.sid, "uploads/img-a.png", b"\x89PNG\r\n\x1a\n")
    )
    await asyncio.sleep(0)
    request = transport.sent[-1]
    assert request["t"] == "file.put"
    assert request["sid"] == screen.sid
    assert request["path"] == "uploads/img-a.png"
    assert base64.b64decode(request["data"]) == b"\x89PNG\r\n\x1a\n"

    await hub.on_device_message(
        "dev1",
        {
            "t": "file.result",
            "sid": screen.sid,
            "id": request["id"],
            "value": {"ok": True},
        },
    )
    assert await pending == {"ok": True}


async def test_screens_in_project_only_counts_online():
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    args = _screen_args()
    project = args["project_id"]
    screen = await hub.open_screen("dev1", ["claude"], **args)
    assert hub.screens_in_project(project) == [screen]
    await hub.detach_device("dev1", t)
    assert hub.screens_in_project(project) == []


async def test_adopt_screen_reregisters_running_screen_after_restart():
    # After a server restart the device re-announces the screens it kept
    # alive; adopting rebinds sid + screen token to the agent identity so viewers and
    # attribution work again without restarting the screen.
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    agent = uuid.uuid4()
    project, topic = uuid.uuid4(), uuid.uuid4()
    screen = hub.adopt_screen(
        "dev1",
        "s-kept",
        token="kept-tok",
        agent_user_id=agent,
        agent_handle="agent-x",
        project_id=project,
        topic_id=topic,
    )
    # Discoverable by sid and by its token (attribution) again.
    assert hub.screen("s-kept") is screen
    assert hub.screen_by_token("kept-tok") is screen
    assert screen.agent_user_id == agent and screen.topic_id == topic
    # Idempotent: adopting the same sid returns the already-registered screen.
    again = hub.adopt_screen(
        "dev1",
        "s-kept",
        token="kept-tok",
        agent_user_id=agent,
        agent_handle="agent-x",
        project_id=project,
        topic_id=topic,
    )
    assert again is screen
    with pytest.raises(ValueError, match="identity"):
        hub.adopt_screen(
            "other-device",
            "s-kept",
            token="kept-tok",
            agent_user_id=agent,
            agent_handle="agent-x",
            project_id=project,
            topic_id=topic,
        )


async def test_inbound_frame_updates_last_seen():
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    await hub.on_device_message("dev1", {"t": "heartbeat"})
    assert hub._device("dev1").last_seen > 0


async def test_screens_for_topic_spans_devices_online_or_not():
    """The lifecycle reaper reverse-looks-up a topic's screen to free it. topic_id is
    globally unique, so the lookup returns every device's screen for it — including
    one on a device that never attached a transport (offline)."""
    hub = DeviceHub()
    await hub.attach_device("dev1", FakeDeviceTransport())
    topic = uuid.uuid4()
    a = await hub.open_screen(
        "dev1", ["claude"], **{**_screen_args(), "topic_id": topic}
    )
    # dev2 has no transport (never attached) — its screen is still registered.
    b = await hub.open_screen(
        "dev2", ["claude"], **{**_screen_args(), "topic_id": topic}
    )
    other = await hub.open_screen("dev1", ["claude"], **_screen_args())

    found = {s.sid for s in hub.screens_for_topic(topic)}
    assert found == {a.sid, b.sid}
    assert other.sid not in found  # a different topic is not swept in


async def test_reassert_screen_resends_adopt_create_under_same_identity():
    """Re-asserting an existing screen re-sends session.create with `adopt` and the
    SAME sid + screen token (the cli's re-provision path): a live device session
    keeps running; one lost to a connector restart — or to a create that was never
    delivered — is respawned without changing the screen's identity."""
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    screen = await hub.open_screen("dev1", ["claude"], **_screen_args())

    await hub.reassert_screen(screen, command=["claude", "--new"], env={"K": "V"})
    create = t.sent[-1]
    assert create["t"] == "session.create" and create["adopt"] is True
    assert create["sid"] == screen.sid and create["screen"] == screen.token
    assert create["command"] == ["claude", "--new"]
    assert create["env"] == {"K": "V"}
    # The registry still resolves the screen by the same sid and token.
    assert hub.screen(screen.sid) is screen
    assert hub.screen_by_token(screen.token) is screen
    assert screen.command == ["claude", "--new"]


async def test_session_error_is_logged_with_its_reason(caplog):
    """The one frame that says WHY a screen failed to start must not vanish: it is
    kept in the log (there is no pending future to fail — the turn surfaces the loss
    as a prompt timeout)."""
    import logging

    hub = DeviceHub()
    await hub.attach_device("dev1", FakeDeviceTransport())
    screen = await hub.open_screen("dev1", ["claude"], **_screen_args())
    with caplog.at_level(logging.WARNING, logger="app.domain.agent.device_hub"):
        await hub.on_device_message(
            "dev1",
            {"t": "session.error", "sid": screen.sid, "error": "terminal: spawn boom"},
        )
    assert any(
        screen.sid in r.getMessage() and "terminal: spawn boom" in r.getMessage()
        for r in caplog.records
    )


# --- connector staleness -------------------------------------------------------
# A connector drops a frame it does not recognise without answering it, so a
# machine left behind by a compatibly-added capability looks healthy right up
# until something times out somewhere unrelated. `hello` is the only moment the
# server can see which binary it is talking to, and `update` is the only frame
# old enough that every build understands it.


def _publish(tmp_path, target: str, payload: bytes) -> str:
    binary = tmp_path / target / "cheesehost"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


async def test_connector_that_names_no_build_is_told_to_update(tmp_path, monkeypatch):
    monkeypatch.setattr(connector_build, "dist_dir", lambda: tmp_path)
    _publish(tmp_path, "linux-amd64", b"current build")
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    await hub.on_device_message("dev1", {"t": "hello", "v": 1})
    assert t.sent[-1] == {"t": "update"}


async def test_connector_that_names_no_build_is_left_alone_with_nothing_to_serve(
    tmp_path, monkeypatch
):
    """Telling a machine to fetch a build we do not have costs it a failed
    download on every reconnect and fixes nothing."""
    monkeypatch.setattr(connector_build, "dist_dir", lambda: tmp_path)
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    await hub.on_device_message("dev1", {"t": "hello", "v": 1})
    assert t.sent == [{"t": "welcome", "v": 1}]


async def test_connector_running_our_bytes_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(connector_build, "dist_dir", lambda: tmp_path)
    digest = _publish(tmp_path, "linux-amd64", b"current build")
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    await hub.on_device_message(
        "dev1", {"t": "hello", "v": 1, "build": digest, "target": "linux-amd64"}
    )
    assert t.sent == [{"t": "welcome", "v": 1}]


async def test_connector_running_other_bytes_is_told_to_update(tmp_path, monkeypatch):
    monkeypatch.setattr(connector_build, "dist_dir", lambda: tmp_path)
    _publish(tmp_path, "linux-amd64", b"current build")
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    await hub.on_device_message(
        "dev1", {"t": "hello", "v": 1, "build": "d" * 64, "target": "linux-amd64"}
    )
    assert t.sent[-1] == {"t": "update"}


async def test_connector_that_cannot_hash_itself_is_left_alone(tmp_path, monkeypatch):
    """It named a platform, so it is new enough to be identified; without a
    digest there is nothing to compare. Updating on that half-answer would
    re-exec the machine on every reconnect and never converge."""
    monkeypatch.setattr(connector_build, "dist_dir", lambda: tmp_path)
    _publish(tmp_path, "linux-amd64", b"current build")
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    await hub.on_device_message("dev1", {"t": "hello", "v": 1, "target": "linux-amd64"})
    assert t.sent == [{"t": "welcome", "v": 1}]


async def test_update_is_pushed_once_per_connection_and_again_on_reconnect(
    tmp_path, monkeypatch
):
    """A self-update that fails leaves the machine on the build it has. Saying so
    once per connection retries at the machine's own reconnect cadence instead of
    on every frame of a connection where the answer cannot change."""
    monkeypatch.setattr(connector_build, "dist_dir", lambda: tmp_path)
    _publish(tmp_path, "linux-amd64", b"current build")
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    await hub.on_device_message("dev1", {"t": "hello", "v": 1})
    await hub.on_device_message("dev1", {"t": "hello", "v": 1})
    assert [m for m in t.sent if m["t"] == "update"] == [{"t": "update"}]

    await hub.detach_device("dev1", t)
    t2 = FakeDeviceTransport()
    await hub.attach_device("dev1", t2)
    await hub.on_device_message("dev1", {"t": "hello", "v": 1})
    assert t2.sent[-1] == {"t": "update"}


class DyingTransport(FakeDeviceTransport):
    """A socket whose peer vanishes mid-life: writes start failing, but the
    receive loop hears nothing, so nobody calls ``detach_device``."""

    def __init__(self) -> None:
        super().__init__()
        self.alive = True

    async def send_json(self, msg: dict) -> None:
        if not self.alive:
            raise ConnectionError(
                'Cannot call "send" once a close message has been sent.'
            )
        self.sent.append(msg)


async def test_a_failed_send_takes_the_device_offline_and_leaks_nothing():
    hub = DeviceHub()
    transport = DyingTransport()
    await hub.attach_device("dev", transport)
    await hub.on_device_message("dev", {"t": "hello", "executor": True})
    waiting = asyncio.create_task(hub.call_executor("dev", "/state", "ping", {}))
    await asyncio.sleep(0)
    assert not waiting.done()
    transport.alive = False
    # The first write into the dead link fails at once, not after the timeout.
    with pytest.raises(DeviceOffline):
        await asyncio.wait_for(hub.exec("dev", ["true"], timeout=30), 1)
    assert not hub.is_online("dev") and "dev" not in hub.online_device_ids()
    # Work that was waiting on the link learns it is gone, and nothing waits on.
    with pytest.raises(DeviceOffline):
        await waiting
    assert hub._device("dev").exec_pending == {}
    assert hub._device("dev").executor_pending == {}
    # A later caller does not even try the dead link.
    with pytest.raises(DeviceOffline):
        await hub.exec("dev", ["true"], timeout=1)
    # The machine dialing back in is fully usable again.
    fresh = FakeDeviceTransport()
    await hub.attach_device("dev", fresh)
    assert hub.is_online("dev")
    pending = asyncio.create_task(hub.exec("dev", ["true"], timeout=1))
    await asyncio.sleep(0)
    assert fresh.sent[-1]["t"] == "exec"
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
