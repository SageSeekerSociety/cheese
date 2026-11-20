"""A sandbox's platform socket, as the hub and its relay see it: the request
arrives as machine.* frames tagged with the state it came in on, and the answer
goes back down the same stream (`device_hub.machine`, `api.machine_relay`)."""

import asyncio
import base64

import httpx
import pytest
from fastapi import FastAPI, Request

from app.api import machine_relay
from app.core.config import settings
from app.domain.agent.device_hub import DeviceHub
from app.domain.agent.device_hub import machine as machine_streams

STATE = "$HOME/.cheese/home/p/r/.cheese/executor"


class FakeDeviceTransport:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)


@pytest.fixture
def handled(monkeypatch):
    """The requests the hub hands on, answered with a fixed body."""
    seen: list[machine_streams.MachineRequest] = []

    async def handler(request, write):
        seen.append(request)
        await write(b"HTTP/1.1 200 OK\r\nconnection: close\r\n\r\n")
        await write("答".encode())

    machine_streams.serve(handler)
    yield seen
    machine_streams.serve(None)


async def _linked() -> tuple[DeviceHub, FakeDeviceTransport]:
    hub, transport = DeviceHub(), FakeDeviceTransport()
    await hub.attach_device("dev", transport)
    transport.sent.clear()
    return hub, transport


def _data(stream: str, payload: bytes) -> dict:
    return {
        "t": "machine.data",
        "id": stream,
        "data": base64.b64encode(payload).decode(),
    }


async def _settled(transport: FakeDeviceTransport, stream: str) -> list[dict]:
    for _ in range(100):
        if any(m["t"] == "machine.close" and m["id"] == stream for m in transport.sent):
            break
        await asyncio.sleep(0.01)
    return [m for m in transport.sent if m.get("id") == stream]


def _answer(frames: list[dict]) -> bytes:
    return b"".join(
        base64.b64decode(m["data"]) for m in frames if m["t"] == "machine.data"
    )


async def test_a_request_split_across_frames_is_handed_on_with_the_state_it_came_in_on(
    handled,
):
    hub, transport = await _linked()
    await hub.on_device_message("dev", {"t": "machine.open", "id": "s1", "path": STATE})
    request = b"POST /v1/messages HTTP/1.1\r\nhost: x\r\ncontent-length: 4\r\n\r\nbody"
    await hub.on_device_message("dev", _data("s1", request[:20]))
    assert handled == []
    await hub.on_device_message("dev", _data("s1", request[20:]))

    frames = await _settled(transport, "s1")
    assert [m["t"] for m in frames][-1] == "machine.close"
    assert _answer(frames) == "HTTP/1.1 200 OK\r\nconnection: close\r\n\r\n答".encode()
    (seen,) = handled
    assert (seen.device_id, seen.state, seen.method, seen.target, seen.body) == (
        "dev",
        STATE,
        "POST",
        "/v1/messages",
        b"body",
    )


async def test_nothing_in_the_request_can_name_another_state(handled):
    """The only state is the one the connector tagged the stream with."""
    hub, transport = await _linked()
    await hub.on_device_message("dev", {"t": "machine.open", "id": "s1", "path": STATE})
    await hub.on_device_message(
        "dev",
        _data(
            "s1",
            b"GET /whoami HTTP/1.1\r\nhost: x\r\n"
            b"x-machine-state: /someone/else\r\n\r\n",
        ),
    )
    await _settled(transport, "s1")
    assert handled[0].state == STATE


async def test_a_stream_nobody_serves_is_told_so(monkeypatch):
    machine_streams.serve(None)
    hub, transport = await _linked()
    await hub.on_device_message("dev", {"t": "machine.open", "id": "s1", "path": STATE})
    await hub.on_device_message(
        "dev", _data("s1", b"GET / HTTP/1.1\r\nhost: x\r\n\r\n")
    )
    assert _answer(await _settled(transport, "s1")).startswith(b"HTTP/1.1 503")


async def test_what_is_not_http_is_refused(handled):
    hub, transport = await _linked()
    await hub.on_device_message("dev", {"t": "machine.open", "id": "s1", "path": STATE})
    await hub.on_device_message("dev", _data("s1", b"\x00\x01 not a request\r\n\r\n"))
    assert _answer(await _settled(transport, "s1")).startswith(b"HTTP/1.1 400")
    assert handled == []


async def test_a_request_too_large_is_refused_not_held(handled, monkeypatch):
    monkeypatch.setattr(machine_streams, "MAX_REQUEST_BYTES", 64)
    hub, transport = await _linked()
    await hub.on_device_message("dev", {"t": "machine.open", "id": "s1", "path": STATE})
    await hub.on_device_message(
        "dev",
        _data("s1", b"POST / HTTP/1.1\r\ncontent-length: 500\r\n\r\n" + b"x" * 100),
    )
    assert _answer(await _settled(transport, "s1")).startswith(b"HTTP/1.1 413")
    assert handled == []


async def test_a_stream_ends_with_the_link():
    hub, transport = await _linked()
    await hub.on_device_message("dev", {"t": "machine.open", "id": "s1", "path": STATE})
    device = hub._device("dev")
    assert "s1" in device.machine_streams
    await hub.detach_device("dev", transport)
    assert device.machine_streams == {}


async def test_the_relay_names_the_caller_itself_and_drops_what_the_sandbox_claimed(
    monkeypatch,
):
    monkeypatch.setattr(settings, "device_connection_secret", "owner-secret")
    backend = FastAPI()
    heard: dict = {}

    @backend.post("/machine/v1/messages")
    async def echo(request: Request):
        heard.update(request.headers)
        heard["body"] = await request.body()
        return {"ok": True}

    relay = machine_relay.forwarder(
        httpx.AsyncClient(transport=httpx.ASGITransport(app=backend)), "http://backend"
    )
    written = bytearray()

    async def write(data: bytes) -> None:
        written.extend(data)

    await relay(
        machine_streams.MachineRequest(
            device_id="dev",
            state=STATE,
            method="POST",
            target="/v1/messages",
            headers=[
                ("content-type", "application/json"),
                ("x-machine-state", "/someone/else"),
                ("x-machine-device", "other"),
                ("x-device-connection-secret", "guessed"),
            ],
            body=b"{}",
        ),
        write,
    )
    assert heard["x-machine-state"] == STATE
    assert heard["x-machine-device"] == "dev"
    assert heard["x-device-connection-secret"] == "owner-secret"
    assert heard["body"] == b"{}"
    head, _, body = bytes(written).partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200")
    assert b"connection: close" in head.lower()
    assert body == b'{"ok":true}'


async def test_a_header_byte_outside_utf8_costs_nothing_but_that_request(handled):
    """One odd byte from one sandbox must not end the link every session on the
    machine shares: the frame is read on that link's loop."""
    hub, transport = await _linked()
    await hub.on_device_message("dev", {"t": "machine.open", "id": "s1", "path": STATE})
    await hub.on_device_message(
        "dev", _data("s1", b"GET /whoami HTTP/1.1\r\nhost: x\r\nx-a: \xff\r\n\r\n")
    )
    await _settled(transport, "s1")
    assert handled[0].target == "/whoami"


@pytest.mark.parametrize(
    "target",
    ["/../admin", "/%2e%2e/admin", "/a/%2E%2E/b", "/a\\b", "/./x", "relative"],
)
async def test_the_relay_refuses_a_path_that_would_leave_machine(target, monkeypatch):
    """The secret rides with the request, so a path a client or proxy would
    resolve outside /machine is refused before anything is sent."""
    monkeypatch.setattr(settings, "device_connection_secret", "owner-secret")
    sent: list = []

    def anywhere(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    relay = machine_relay.forwarder(
        httpx.AsyncClient(transport=httpx.MockTransport(anywhere)), "http://backend"
    )
    written = bytearray()

    async def write(data: bytes) -> None:
        written.extend(data)

    await relay(
        machine_streams.MachineRequest("dev", STATE, "GET", target, [], b""), write
    )
    assert sent == []
    assert bytes(written).startswith(b"HTTP/1.1 400")


async def test_a_large_body_streams_across_frames_without_waiting_for_the_end(
    monkeypatch,
):
    monkeypatch.setattr(settings, "device_connection_secret", "owner-secret")
    monkeypatch.setattr(machine_streams, "_STREAM_THRESHOLD", 8)
    received: list[bytes] = []
    first_chunk = asyncio.Event()

    backend = FastAPI()

    @backend.post("/machine/api/sandbox/forge/p/repo.git/git-receive-pack")
    async def upload(request: Request):
        async for chunk in request.stream():
            if chunk:
                received.append(chunk)
                first_chunk.set()
        return {"bytes": sum(len(c) for c in received)}

    machine_streams.serve(
        machine_relay.forwarder(
            httpx.AsyncClient(transport=httpx.ASGITransport(app=backend)),
            "http://backend",
        )
    )
    try:
        hub, transport = await _linked()
        await hub.on_device_message(
            "dev", {"t": "machine.open", "id": "s1", "path": STATE}
        )
        head = (
            b"POST /api/sandbox/forge/p/repo.git/git-receive-pack HTTP/1.1\r\n"
            b"host: platform.cheese\r\n"
            b"content-length: 24\r\n\r\n"
        )
        await hub.on_device_message("dev", _data("s1", head + b"0123456789ab"))
        await asyncio.wait_for(first_chunk.wait(), timeout=2.0)
        assert b"".join(received) == b"0123456789ab"
        await hub.on_device_message("dev", _data("s1", b"cdefghijklmn"))
        frames = await _settled(transport, "s1")
        assert b"".join(received) == b"0123456789abcdefghijklmn"
        assert _answer(frames).endswith(b'{"bytes":24}')
    finally:
        machine_streams.serve(None)
