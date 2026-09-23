"""Preview WebSocket cleanup when the browser disconnects."""

import asyncio
import uuid
from unittest.mock import AsyncMock, Mock

from starlette.websockets import WebSocket, WebSocketDisconnect

from app.api.routes import app_preview
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import preview_hub


def test_hmr_cleanup_ignores_a_peer_gone_during_close(monkeypatch):
    stream = Mock()
    stream.send = AsyncMock()
    stream.receive = AsyncMock(return_value=(wire.OP_WS_OK, wire.encode_meta({})))
    monkeypatch.setattr(preview_hub, "open_stream", lambda _topic_id: stream)
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
    asyncio.run(app_preview.relay_ws(websocket, uuid.uuid4()))

    assert sent == ["websocket.accept", "websocket.close"]
    stream.close.assert_called_once_with()
