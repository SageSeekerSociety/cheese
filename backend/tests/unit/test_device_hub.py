"""DeviceHub: the server end of the link.Msg protocol (I/O-free, fake transports)."""

import base64
import uuid

from app.domain.agent.device_hub import DeviceHub, HubScreen


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


async def test_open_screen_sends_session_create_and_registers_token():
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    screen = await hub.open_screen(
        "dev1", ["claude"], "//js", env={"K": "V"}, **_screen_args()
    )
    create = t.sent[-1]
    assert create["t"] == "session.create"
    assert create["command"] == ["claude"] and create["source"] == "//js"
    assert create["env"] == {"K": "V"} and create["screen"] == screen.token
    # The screen is discoverable by its token (attribution) and by sid.
    assert hub.screen_by_token(screen.token) is screen
    assert hub.screen(screen.sid) is screen


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
    screen = await hub.open_screen("dev1", ["claude"], "//js", **_screen_args())
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
    screen = await hub.open_screen("dev1", ["claude"], "//js", **_screen_args())
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


async def test_exposed_screen_fn_answered_via_rpc_result():
    async def echo(hub: DeviceHub, screen: HubScreen, args: list) -> dict:
        return {"echo": args}

    hub = DeviceHub(screen_fns={"echo": echo})
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    screen = await hub.open_screen("dev1", ["claude"], "//js", **_screen_args())
    await hub.on_device_message(
        "dev1",
        {"t": "rpc.call", "sid": screen.sid, "id": "c1", "name": "echo", "args": [1]},
    )
    reply = t.sent[-1]
    assert reply["t"] == "rpc.result" and reply["id"] == "c1"
    assert reply["value"] == {"echo": [1]} and reply["error"] == ""


async def test_screens_in_project_only_counts_online():
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    args = _screen_args()
    project = args["project_id"]
    screen = await hub.open_screen("dev1", ["claude"], "//js", **args)
    assert hub.screens_in_project(project) == [screen]
    await hub.detach_device("dev1", t)
    assert hub.screens_in_project(project) == []


async def test_device_disconnect_drops_its_screen_subscription():
    from app.domain.agent.hook_events import HookRouter
    from app.domain.agent.hooks_substrate import HooksTurnProvider

    hub = DeviceHub()
    transport = FakeDeviceTransport()
    await hub.attach_device("dev1", transport)
    screen = await hub.open_screen("dev1", ["claude"], "//js", **_screen_args())
    assert screen.project_id is not None and screen.topic_id is not None

    router = HookRouter()
    provider = HooksTurnProvider(router=router)
    subscription = await provider.ensure_subscription(
        screen.project_id, screen.topic_id
    )
    provider._live[screen.topic_id] = screen

    await hub.detach_device("dev1", transport)

    assert subscription.consumer_task is not None
    assert subscription.consumer_task.done()
    assert router.push(str(screen.topic_id), {"hook_event_name": "Stop"}) is False


async def test_adopt_screen_reregisters_running_screen_after_restart():
    # After a server restart the device (frozen cli) re-announces the screens it kept
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
        "dev1", "s-kept", token="other", agent_user_id=uuid.uuid4(), agent_handle="y"
    )
    assert again is screen


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
        "dev1", ["claude"], "//js", **{**_screen_args(), "topic_id": topic}
    )
    # dev2 has no transport (never attached) — its screen is still registered.
    b = await hub.open_screen(
        "dev2", ["claude"], "//js", **{**_screen_args(), "topic_id": topic}
    )
    other = await hub.open_screen("dev1", ["claude"], "//js", **_screen_args())

    found = {s.sid for s in hub.screens_for_topic(topic)}
    assert found == {a.sid, b.sid}
    assert other.sid not in found  # a different topic is not swept in


async def test_reassert_screen_resends_adopt_create_under_same_identity():
    """Re-asserting an existing screen re-sends session.create with `adopt` and the
    SAME sid + screen token (the cli's re-provision path): a live device session
    hot-reloads the cheeselet; one lost to a connector restart — or to a create that
    was never delivered — is respawned without changing the screen's identity."""
    hub = DeviceHub()
    t = FakeDeviceTransport()
    await hub.attach_device("dev1", t)
    screen = await hub.open_screen("dev1", ["claude"], "//js", **_screen_args())

    await hub.reassert_screen(
        screen, command=["claude", "--new"], cheeselet_source="//js2", env={"K": "V"}
    )
    create = t.sent[-1]
    assert create["t"] == "session.create" and create["adopt"] is True
    assert create["sid"] == screen.sid and create["screen"] == screen.token
    assert create["command"] == ["claude", "--new"] and create["source"] == "//js2"
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
    screen = await hub.open_screen("dev1", ["claude"], "//js", **_screen_args())
    with caplog.at_level(logging.WARNING, logger="app.domain.agent.device_hub"):
        await hub.on_device_message(
            "dev1",
            {"t": "session.error", "sid": screen.sid, "error": "terminal: spawn boom"},
        )
    assert any(
        screen.sid in r.getMessage() and "terminal: spawn boom" in r.getMessage()
        for r in caplog.records
    )
