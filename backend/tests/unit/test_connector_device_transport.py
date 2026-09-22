"""The WebSocket adapter tells the hub, in the hub's terms, when a link is gone."""

from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.api.routes import connector
from app.api.routes.connector import _WebSocketDeviceTransport
from app.domain.device.owner_reads import DeviceIdentity


class ClosedSocket:
    async def send_json(self, msg: dict) -> None:
        raise RuntimeError('Cannot call "send" once a close message has been sent.')


class DroppedSocket:
    async def send_json(self, msg: dict) -> None:
        raise WebSocketDisconnect(code=1006)


@pytest.mark.parametrize("socket", [ClosedSocket(), DroppedSocket()])
async def test_a_dead_socket_reports_a_lost_connection(socket):
    with pytest.raises(ConnectionError):
        await _WebSocketDeviceTransport(socket).send_json({"t": "welcome"})  # type: ignore[arg-type]


@pytest.mark.parametrize("failure", ["send", "receive", "handler"])
async def test_device_route_cleans_up_a_disconnected_link(monkeypatch, failure):
    """Exercise Starlette's real state transitions after a failed ASGI send."""
    incoming = iter(
        [
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": '{"t":"ping"}'},
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )

    async def receive():
        return next(incoming)

    async def send(message):
        if message["type"] == "websocket.send":
            raise OSError("peer closed")

    websocket = WebSocket({"type": "websocket"}, receive, send)
    transport = None

    async def attach(device_id, attached, *, name):
        nonlocal transport
        transport = attached

    async def on_message(device_id, message):
        if failure == "handler":
            raise RuntimeError("unexpected handler failure")
        if failure == "send":
            with pytest.raises(ConnectionError):
                await transport.send_json({"t": "pong"})

    detach = AsyncMock()
    monkeypatch.setattr(
        connector.owner_reads,
        "device_for_token",
        AsyncMock(return_value=DeviceIdentity("test-device", "test-device")),
    )
    monkeypatch.setattr(connector.settings, "device_connection_owner", True)
    monkeypatch.setattr(connector.device_hub, "attach_device", attach)
    monkeypatch.setattr(connector.device_hub, "on_device_message", on_message)
    monkeypatch.setattr(connector.device_hub, "detach_device", detach)
    if failure == "handler":
        with pytest.raises(RuntimeError, match="unexpected handler failure"):
            await connector.agent_socket(websocket, AsyncMock(), token="test-token")
    else:
        await connector.agent_socket(websocket, AsyncMock(), token="test-token")
    detach.assert_awaited_once_with("test-device", transport)
