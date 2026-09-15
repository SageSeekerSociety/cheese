"""The WebSocket adapter tells the hub, in the hub's terms, when a link is gone."""

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api.routes.connector import _WebSocketDeviceTransport


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
