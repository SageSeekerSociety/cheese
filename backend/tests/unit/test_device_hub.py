"""Unit tests for ``DeviceHub`` — the server end of the frozen link.Msg protocol.

Exercised with in-process fake transports (no WebSocket, no device). Every assertion
pins a field of the frozen wire contract so a drift from ``misc/web-claude/server``
is caught here.
"""

import base64
from typing import Any

import pytest

from app.agent.hub import PROTOCOL_VERSION, DeviceHub

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeDevice:
    """Captures every JSON message the hub sends to the device."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)

    def last(self, t: str) -> dict[str, Any]:
        for msg in reversed(self.sent):
            if msg.get("t") == t:
                return msg
        raise AssertionError(f"no {t!r} message sent; got {[m.get('t') for m in self.sent]}")


class FakeViewer:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    async def send_bytes(self, data: bytes) -> None:
        self.frames.append(data)


async def test_attach_device_sends_welcome_with_version() -> None:
    hub = DeviceHub()
    dev = FakeDevice()
    await hub.attach_device("d1", dev)
    assert dev.last("welcome") == {"t": "welcome", "v": PROTOCOL_VERSION}
    assert hub.is_online("d1")


async def test_open_screen_ships_session_create_with_token_and_source() -> None:
    hub = DeviceHub()
    dev = FakeDevice()
    await hub.attach_device("d1", dev)
    screen = await hub.open_screen(
        "d1", ["bash", "-lc", "exec claude"], "CHEESELET_SRC", project_id=1, agent_user_id=1, cols=100, rows=40
    )

    msg = dev.last("session.create")
    assert msg["sid"] == screen.sid
    assert msg["command"] == ["bash", "-lc", "exec claude"]
    assert msg["screen"] == screen.token
    assert msg["source"] == "CHEESELET_SRC"
    assert (msg["cols"], msg["rows"]) == (100, 40)
    # The screen is resolvable by its injected token (the CHEESE_SCREEN attribution key).
    assert hub.screen_by_token(screen.token) is screen
    assert hub.screen_by_token("nope") is None


async def test_open_screen_injects_env() -> None:
    hub = DeviceHub()
    dev = FakeDevice()
    await hub.attach_device("d1", dev)
    await hub.open_screen(
        "d1", ["x"], "src", project_id=1, agent_user_id=1, env={"CHEESE_TOKEN": "agent-jwt"}
    )
    msg = dev.last("session.create")
    assert msg["env"] == {"CHEESE_TOKEN": "agent-jwt"}
    # No env → no env key (the default screen has none).
    await hub.open_screen("d1", ["x"], "src", project_id=1, agent_user_id=1)
    assert "env" not in dev.last("session.create")


async def test_screens_in_project_lists_only_online_matching() -> None:
    hub = DeviceHub()
    d1 = FakeDevice()
    await hub.attach_device("d1", d1)
    a = await hub.open_screen("d1", ["x"], "src", project_id=7, agent_user_id=1)
    await hub.open_screen("d1", ["x"], "src", project_id=9, agent_user_id=2)  # other project
    assert [s.sid for s in hub.screens_in_project(7)] == [a.sid]

    # A screen on an offline device is excluded.
    await hub.open_screen("d2", ["x"], "src", project_id=7, agent_user_id=3)  # d2 never attached
    assert [s.sid for s in hub.screens_in_project(7)] == [a.sid]


async def test_close_screen_ends_session_and_forgets_it() -> None:
    hub = DeviceHub()
    dev = FakeDevice()
    await hub.attach_device("d1", dev)
    screen = await hub.open_screen("d1", ["x"], "src", project_id=1, agent_user_id=1)

    assert await hub.close_screen("d1", screen.sid) is True
    assert dev.last("session.close")["sid"] == screen.sid
    assert hub.screen(screen.sid) is None
    assert hub.screen_by_token(screen.token) is None
    # Closing again (unknown) returns False.
    assert await hub.close_screen("d1", screen.sid) is False


async def test_var_push_updates_screen_vars() -> None:
    hub = DeviceHub()
    dev = FakeDevice()
    await hub.attach_device("d1", dev)
    screen = await hub.open_screen("d1", ["x"], "src", project_id=1, agent_user_id=1)
    await hub.on_device_message("d1", {"t": "var.push", "sid": screen.sid, "name": "busy", "value": True})
    assert screen.vars["busy"] is True


async def test_screen_data_is_base64_decoded_and_fanned_out() -> None:
    hub = DeviceHub()
    dev = FakeDevice()
    await hub.attach_device("d1", dev)
    screen = await hub.open_screen("d1", ["x"], "src", project_id=1, agent_user_id=1)
    v1, v2 = FakeViewer(), FakeViewer()
    await hub.attach_viewer("d1", screen.sid, v1)
    await hub.attach_viewer("d1", screen.sid, v2)

    payload = b"\x1b[2Jhello"
    await hub.on_device_message(
        "d1", {"t": "screen.data", "sid": screen.sid, "data": base64.b64encode(payload).decode()}
    )
    assert v1.frames == [payload]
    assert v2.frames == [payload]


async def test_first_viewer_subscribes_last_viewer_unsubscribes() -> None:
    hub = DeviceHub()
    dev = FakeDevice()
    await hub.attach_device("d1", dev)
    screen = await hub.open_screen("d1", ["x"], "src", project_id=1, agent_user_id=1)

    v1, v2 = FakeViewer(), FakeViewer()
    await hub.attach_viewer("d1", screen.sid, v1, cols=90, rows=30)
    sub = dev.last("screen.subscribe")
    assert (sub["cols"], sub["rows"]) == (90, 30)

    before = len([m for m in dev.sent if m.get("t") == "screen.subscribe"])
    await hub.attach_viewer("d1", screen.sid, v2)  # second viewer: no new subscribe
    after = len([m for m in dev.sent if m.get("t") == "screen.subscribe"])
    assert after == before

    await hub.detach_viewer("d1", screen.sid, v1)  # still one viewer left
    assert not any(m.get("t") == "screen.unsubscribe" for m in dev.sent)
    await hub.detach_viewer("d1", screen.sid, v2)  # last one leaves
    assert dev.last("screen.unsubscribe")["sid"] == screen.sid


async def test_viewer_input_resize_control() -> None:
    hub = DeviceHub()
    dev = FakeDevice()
    await hub.attach_device("d1", dev)
    screen = await hub.open_screen("d1", ["x"], "src", project_id=1, agent_user_id=1)

    await hub.viewer_input("d1", screen.sid, b"ls\r")
    inp = dev.last("screen.input")
    assert base64.b64decode(inp["data"]) == b"ls\r"

    await hub.viewer_resize("d1", screen.sid, 80, 24)
    assert dev.last("screen.resize") == {"t": "screen.resize", "sid": screen.sid, "cols": 80, "rows": 24}

    await hub.viewer_control("d1", screen.sid, "full")
    assert dev.last("var.set")["value"] == "full"
    await hub.viewer_control("d1", screen.sid, "bogus")  # unknown level clamps to passive
    assert dev.last("var.set")["value"] == "passive"


async def test_rpc_call_screen_ready_returns_ok() -> None:
    hub = DeviceHub()
    dev = FakeDevice()
    await hub.attach_device("d1", dev)
    screen = await hub.open_screen("d1", ["x"], "src", project_id=1, agent_user_id=1)
    await hub.on_device_message(
        "d1", {"t": "rpc.call", "sid": screen.sid, "id": "c1", "name": "screenReady", "args": []}
    )
    result = dev.last("rpc.result")
    assert result["id"] == "c1"
    assert result["value"] == {"ok": True}
    assert result["error"] == ""


async def test_rpc_call_unknown_function_returns_error() -> None:
    hub = DeviceHub()
    dev = FakeDevice()
    await hub.attach_device("d1", dev)
    screen = await hub.open_screen("d1", ["x"], "src", project_id=1, agent_user_id=1)
    await hub.on_device_message(
        "d1", {"t": "rpc.call", "sid": screen.sid, "id": "c2", "name": "nope", "args": []}
    )
    result = dev.last("rpc.result")
    assert result["value"] is None
    assert "unknown function" in result["error"]


async def test_custom_screen_fn_is_dispatched() -> None:
    calls: list[list[Any]] = []

    async def echo(hub: DeviceHub, screen: Any, args: list[Any]) -> Any:
        calls.append(args)
        return {"echo": args}

    hub = DeviceHub(screen_fns={"echo": echo})
    dev = FakeDevice()
    await hub.attach_device("d1", dev)
    screen = await hub.open_screen("d1", ["x"], "src", project_id=1, agent_user_id=1)
    await hub.on_device_message(
        "d1", {"t": "rpc.call", "sid": screen.sid, "id": "c3", "name": "echo", "args": [1, 2]}
    )
    assert calls == [[1, 2]]
    assert dev.last("rpc.result")["value"] == {"echo": [1, 2]}


async def test_exec_roundtrip_resolves_on_result() -> None:
    import asyncio

    hub = DeviceHub()
    dev = FakeDevice()
    await hub.attach_device("d1", dev)

    task = asyncio.ensure_future(hub.exec("d1", ["echo", "hi"], timeout=5))
    await asyncio.sleep(0)  # let exec send its request
    sent = dev.last("exec")
    assert sent["command"] == ["echo", "hi"]
    assert sent["timeout"] == 5
    eid = sent["id"]

    await hub.on_device_message(
        "d1", {"t": "exec.result", "id": eid, "stdout": "hi\n", "exit": 0, "truncated": False}
    )
    result = await task
    assert result == {"stdout": "hi\n", "stderr": "", "exit": 0, "truncated": False}


async def test_exec_stdin_and_env_are_forwarded() -> None:
    import asyncio

    hub = DeviceHub()
    dev = FakeDevice()
    await hub.attach_device("d1", dev)
    task = asyncio.ensure_future(
        hub.exec("d1", ["cat"], stdin="data", env={"A": "b"}, cwd="/tmp", timeout=3)
    )
    await asyncio.sleep(0)
    sent = dev.last("exec")
    assert sent["stdin"] == "data"
    assert sent["env"] == {"A": "b"}
    assert sent["cwd"] == "/tmp"
    await hub.on_device_message("d1", {"t": "exec.result", "id": sent["id"], "stdout": "data"})
    await task


async def test_exec_timeout_cancels_on_device() -> None:
    import asyncio

    hub = DeviceHub()
    dev = FakeDevice()
    await hub.attach_device("d1", dev)
    # timeout=0 → wait_for(fut, 5) still waits; force it by never answering and a tiny margin.
    # Use a monkeypatched short margin via timeout so the test stays fast.
    task = asyncio.ensure_future(hub.exec("d1", ["sleep", "100"], timeout=-4.9))
    with pytest.raises(asyncio.TimeoutError):
        await task
    assert any(m.get("t") == "exec.cancel" for m in dev.sent)


async def test_concurrent_sends_to_one_device_are_serialized() -> None:
    import asyncio

    class ReentrancyGuard:
        """Yields mid-send; if two sends run concurrently the counter exceeds 1."""

        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.sent: list[dict[str, Any]] = []

        async def send_json(self, msg: dict[str, Any]) -> None:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0)  # a real WebSocket awaits here — the interleave window
            self.sent.append(msg)
            self.active -= 1

    hub = DeviceHub()
    guard = ReentrancyGuard()
    await hub.attach_device("d1", guard)  # welcome
    # Fire many screen opens at once; each does one device send.
    await asyncio.gather(*(hub.open_screen("d1", ["x"], "src", project_id=1, agent_user_id=1) for _ in range(10)))

    assert guard.max_active == 1  # the send lock serialized every write
    assert len(guard.sent) == 11  # welcome + 10 session.create, none dropped


async def test_hello_records_proto_and_skew_hook() -> None:
    seen: list[int | None] = []

    class SkewHub(DeviceHub):
        def _on_version_skew(self, device_id: str, proto: int | None) -> None:
            seen.append(proto)

    hub = SkewHub()
    dev = FakeDevice()
    await hub.attach_device("d1", dev)
    await hub.on_device_message("d1", {"t": "hello", "v": PROTOCOL_VERSION})
    assert seen == []  # matching version: no skew
    await hub.on_device_message("d1", {"t": "hello", "v": 999})
    assert seen == [999]  # skew reported, not fatal
