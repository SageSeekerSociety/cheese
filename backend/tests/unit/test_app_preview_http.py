"""Response ownership across disconnect and relay hand-off cancellation.

The real hub, Starlette response and tunnel adapter run against ASGI boundaries.
No upstream socket or content-host authentication is substituted as proof here.
"""

import asyncio
import uuid

from starlette.requests import Request
from starlette.responses import Response
from starlette.websockets import WebSocket

from app.api.routes import app_preview
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import PreviewHub


def _scope():
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": "GET",
        "path": "/stream",
        "raw_path": b"/stream",
        "query_string": b"a=1&a=2&escaped=%2F",
        "headers": [],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("browser", 1234),
    }


class _TunnelSink:
    def __init__(self):
        self.machine = None
        self.frames = []

    async def send_bytes(self, data):
        op, sid, payload = wire.decode(data)
        if op == wire.OP_CLOSE:
            # Flow-control checkpoint on the actual Starlette tunnel adapter.
            await asyncio.sleep(0)
        self.frames.append((op, sid, payload))
        if op == wire.OP_REQ:
            self.machine.on_frame(
                wire.encode(
                    wire.OP_RESP,
                    sid,
                    wire.encode_meta({"status": 200, "headers": []}, b"first"),
                )
            )


async def test_disconnect_delivers_cancel_through_actual_response_and_adapter():
    sink = _TunnelSink()

    async def tunnel_receive():
        return {"type": "websocket.connect"}

    async def tunnel_send(message):
        if message["type"] == "websocket.send":
            await sink.send_bytes(message["bytes"])

    websocket = WebSocket(dict(_scope(), type="websocket"), tunnel_receive, tunnel_send)
    await websocket.accept()
    hub = PreviewHub()
    topic = uuid.uuid4()
    machine = hub.attach(
        topic, "seat", app_preview._WebSocketPreviewTransport(websocket)
    )
    sink.machine = machine
    upstream = await hub.request_stream(
        topic, "seat", method="GET", path="/", headers=[]
    )
    first = asyncio.Event()
    sent = []

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body":
            first.set()

    async def receive():
        await first.wait()
        return {"type": "http.disconnect"}

    await asyncio.wait_for(
        app_preview._PreviewStreamingResponse(upstream)(_scope(), receive, send), 1
    )
    assert sent[0]["type"] == "http.response.start"
    assert sent[1]["body"] == b"first"
    assert sum(op == wire.OP_CLOSE for op, _, _ in sink.frames) == 1
    assert machine.streams == {}


async def test_cancel_during_watcher_cleanup_does_not_leave_unowned_response(
    monkeypatch,
):
    sink = _TunnelSink()
    hub = PreviewHub()
    topic = uuid.uuid4()
    machine = hub.attach(topic, "seat", sink)
    sink.machine = machine
    monkeypatch.setattr(app_preview, "preview_hub", hub)
    cancelled_receive = asyncio.Event()
    body_read = False

    async def receive():
        nonlocal body_read
        if not body_read:
            body_read = True
            return {"type": "http.request", "body": b"", "more_body": False}
        try:
            await asyncio.Event().wait()
        finally:
            cancelled_receive.set()

    task = asyncio.create_task(
        app_preview.relay_http(topic, "seat", Request(_scope(), receive))
    )

    async def expiry():
        await cancelled_receive.wait()
        task.cancel()

    outcomes = await asyncio.wait_for(
        asyncio.gather(task, expiry(), return_exceptions=True), 1
    )
    assert isinstance(outcomes[0], asyncio.CancelledError)
    assert not isinstance(outcomes[0], Response)
    assert machine.streams == {}
    assert sum(op == wire.OP_CLOSE for op, _, _ in sink.frames) == 1


async def test_failed_write_closes_idle_real_adapter_and_joins_route(monkeypatch):
    import pytest

    hub = PreviewHub()
    topic = uuid.uuid4()
    monkeypatch.setattr(app_preview, "preview_hub", hub)
    monkeypatch.setattr(
        app_preview,
        "scoped_token_claims",
        lambda _token: {"t": str(topic), "a": "seat"},
    )
    accepted = asyncio.Event()
    closed = []
    receive_finished = asyncio.Event()
    first = True

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "websocket.connect"}
        try:
            await asyncio.Event().wait()
        finally:
            receive_finished.set()

    async def send(message):
        if message["type"] == "websocket.accept":
            accepted.set()
        elif message["type"] == "websocket.send":
            raise RuntimeError("shared write lost")
        elif message["type"] == "websocket.close":
            closed.append(message)

    ws = WebSocket(dict(_scope(), type="websocket"), receive, send)
    owner = asyncio.create_task(app_preview.preview_tunnel(ws, "fixture"))
    await accepted.wait()
    machine = hub.machine(topic, "seat")
    assert machine is not None
    stream = machine.open()
    with pytest.raises(OSError):
        await stream.send(wire.OP_REQ)
    await asyncio.wait_for(owner, 1)
    assert receive_finished.is_set()
    assert len(closed) == 1 and closed[0]["code"] == 1011
    assert hub.machine(topic, "seat") is None
    assert not hub.is_online(topic, "seat")
    assert machine.open() is None
    assert not machine.pending_cancels
