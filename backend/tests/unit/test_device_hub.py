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
