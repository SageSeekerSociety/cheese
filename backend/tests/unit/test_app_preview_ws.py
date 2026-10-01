"""Preview WebSocket cleanup when the browser disconnects."""

import asyncio
import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.api.routes import app_preview
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import PreviewHub, preview_hub


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
