"""Preview WebSocket cleanup when the browser disconnects."""

import asyncio
import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.api.routes import app_preview
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import PreviewHub, preview_hub


async def test_planned_drain_during_helper_upgrade_never_supersedes_the_helper():
    from fastapi import FastAPI

    from app.core.sandbox_auth import mint_scoped_token

    topic, hub = uuid.uuid4(), PreviewHub()
    application = FastAPI()
    application.state.preview_hub = hub
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)
        if message["type"] == "websocket.accept":
            await hub.shutdown()

    websocket = WebSocket(
        {
            "type": "websocket",
            "path": "/preview/tunnel",
            "headers": [],
            "app": application,
        },
        receive,
        send,
    )
    token = mint_scoped_token(
        project_id=str(uuid.uuid4()), topic_id=str(topic), agent_handle="seat"
    )
    await app_preview.preview_tunnel(websocket, token=token)
    assert sent[-1]["type"] == "websocket.close"
    assert sent[-1]["code"] == 1012


@pytest.mark.parametrize("upstream_status", [404, 409, 500, 503])
async def test_owner_relay_preserves_application_status(upstream_status):
    import httpx
    from fastapi import FastAPI, Request

    topic = uuid.uuid4()
    hub = PreviewHub()
    application = FastAPI()
    application.state.preview_hub = hub

    class Native:
        machine = None

        async def send_bytes(self, data):
            op, sid, _ = wire.decode(data)
            if op == wire.OP_REQ:
                self.machine.on_frame(
                    wire.encode(
                        wire.OP_RESP,
                        sid,
                        wire.encode_meta(
                            {
                                "status": upstream_status,
                                "headers": [],
                            }
                        ),
                    )
                )
                self.machine.on_frame(wire.encode(wire.OP_END, sid))

    native = Native()
    native.machine = hub.attach(topic, "seat", native)

    @application.get("/")
    async def viewer(request: Request):
        return await app_preview.relay_http(topic, "seat", request)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://viewer"
    ) as browser:
        response = await browser.get("/")
    assert response.status_code == upstream_status
    assert "X-Cheese-Preview-State" not in response.headers


@pytest.mark.parametrize("state,status", [("offline", 503), ("gone", 409)])
async def test_owner_relay_distinguishes_admission_from_application_response(
    state, status
):
    import httpx
    from fastapi import FastAPI, Request

    topic = uuid.uuid4()
    hub = PreviewHub()
    application = FastAPI()
    application.state.preview_hub = hub

    class ReplacedListener:
        machine = None
        calls = 0

        async def send_bytes(self, data):
            op, sid, _ = wire.decode(data)
            if op == wire.OP_REQ:
                self.calls += 1
                self.machine.on_frame(
                    wire.encode(wire.OP_ERR, sid, b"preview instance gone")
                )

    native = ReplacedListener()
    if state == "gone":
        native.machine = hub.attach(
            topic, "seat", native, capabilities=frozenset({"instance-v1"})
        )

    @application.post("/")
    async def viewer(request: Request):
        return await app_preview.relay_http(topic, "seat", request, instance="a" * 64)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://viewer"
    ) as browser:
        response = await browser.post("/", content=b"intended mutation")
    assert response.status_code == status
    assert response.headers["X-Cheese-Preview-State"] == (
        "instance_gone" if state == "gone" else "transport_unavailable"
    )
    assert native.calls == (1 if state == "gone" else 0)
    assert ("Retry-After" in response.headers) == (state == "offline")


def test_hmr_cleanup_ignores_a_peer_gone_during_close(monkeypatch):
    stream = Mock()
    stream.send = AsyncMock()
    stream.aclose = AsyncMock()
    stream.receive = AsyncMock(return_value=(wire.OP_WS_OK, wire.encode_meta({})))
    monkeypatch.setattr(preview_hub, "open_stream", lambda _topic_id, _seat: stream)
    monkeypatch.setattr(
        app_preview,
        "_pump",
        AsyncMock(side_effect=WebSocketDisconnect(code=1006)),
    )
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message["type"])
        if message["type"] == "websocket.close":
            raise OSError("peer gone")

    websocket = WebSocket(
        {"type": "websocket", "path": "/@vite/client", "headers": []},
        receive,
        send,
    )
    asyncio.run(app_preview.relay_ws(websocket, uuid.uuid4(), "cheese-a"))

    assert sent == ["websocket.accept", "websocket.close"]
    stream.aclose.assert_awaited_once_with()


@pytest.mark.parametrize("text", [False, True])
@pytest.mark.parametrize("overflow", [False, True])
async def test_browser_message_limit_counts_wire_bytes_and_cancels_stream(
    monkeypatch, text, overflow
):
    monkeypatch.setattr(wire, "MAX_WS_MESSAGE_BYTES", 8)
    frames = []

    class Transport:
        async def send_bytes(self, data):
            frames.append(wire.decode(data))

    hub = PreviewHub()
    machine = hub.attach(uuid.uuid4(), "seat", Transport())
    stream = machine.open()
    connected = False
    sent_message = False
    sent = []
    # The wire type byte counts toward the limit. UTF-8 text uses byte length.
    data = b"x" * (8 if overflow else 7)
    message = (
        {"text": "测" * (3 if overflow else 2) + ("" if overflow else "x")}
        if text
        else {"bytes": data}
    )

    async def receive():
        nonlocal connected, sent_message
        if not connected:
            connected = True
            return {"type": "websocket.connect"}
        if not sent_message:
            sent_message = True
            return {"type": "websocket.receive", **message}
        return {"type": "websocket.disconnect", "code": 1000}

    async def send(message):
        sent.append(message)

    browser = WebSocket(
        {"type": "websocket", "path": "/hmr", "headers": []}, receive, send
    )
    await browser.accept()
    await asyncio.wait_for(app_preview._pump(browser, stream), 1)
    messages = [payload for op, _, payload in frames if op == wire.OP_WS_MSG]
    assert len(messages) == (0 if overflow else 1)
    if not overflow:
        assert len(messages[0]) == 8
    else:
        assert any(m.get("code") == 1009 for m in sent)
    assert sum(op == wire.OP_CLOSE for op, _, _ in frames) == 1
    assert machine.streams == {}


@pytest.mark.parametrize(
    "negotiated,payload,expected",
    [
        (True, wire.close_payload(1013, "稍后重试"), (1013, "稍后重试")),
        (False, b"the machine went away", (1011, "preview upstream disconnected")),
        (True, wire.close_payload(1006), (1002, "invalid close code")),
    ],
)
async def test_relay_preserves_negotiated_close_without_decoding_legacy_text(
    monkeypatch, negotiated, payload, expected
):
    class Transport:
        machine = None

        async def send_bytes(self, data):
            op, sid, _ = wire.decode(data)
            if op == wire.OP_WS_OPEN:
                self.machine.on_frame(
                    wire.encode(wire.OP_WS_OK, sid, wire.encode_meta({}))
                )
                self.machine.on_frame(wire.encode(wire.OP_CLOSE, sid, payload))

    transport = Transport()
    hub = PreviewHub()
    topic = uuid.uuid4()
    machine = hub.attach(
        topic,
        "seat",
        transport,
        capabilities=wire.CAPABILITIES if negotiated else frozenset(),
    )
    transport.machine = machine
    monkeypatch.setattr(app_preview, "preview_hub", hub)
    connected = False
    sent = []

    async def receive():
        nonlocal connected
        if not connected:
            connected = True
            return {"type": "websocket.connect"}
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    websocket = WebSocket(
        {"type": "websocket", "path": "/hmr", "headers": []}, receive, send
    )
    await asyncio.wait_for(app_preview.relay_ws(websocket, topic, "seat"), 1)
    closes = [m for m in sent if m["type"] == "websocket.close"]
    assert len(closes) == 1
    assert (closes[0]["code"], closes[0]["reason"]) == expected
    assert machine.streams == {}
