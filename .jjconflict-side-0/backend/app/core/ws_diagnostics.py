"""Say why a WebSocket handshake was refused.

A refused handshake reaches the browser as a plain close, so the UI can only
report that the connection dropped — and a client retrying every few seconds
looks exactly like an unstable network. That is how a chat socket that had never
once connected (its path missed the route by one `/api`) was reported as 网络不
稳定 rather than as a routing bug.

Starlette closes a WebSocket *before accepting* when no route matches or a
dependency fails; a handler that rejects a caller on purpose has always accepted
first. So close-before-accept is the interesting case, and it is worth a warning
with the path attached — the previous record was an INFO line reading
"connection rejected (403 Forbidden)" with no path and no reason.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("app.ws")

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class LogRefusedWebSockets:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "websocket":
            await self.app(scope, receive, send)
            return

        accepted = False

        async def watched_send(message: dict[str, Any]) -> None:
            nonlocal accepted
            if message.get("type") == "websocket.accept":
                accepted = True
            elif message.get("type") == "websocket.close" and not accepted:
                logger.warning(
                    "websocket refused before accept: no route matched %s "
                    "(or a dependency failed) — the client sees a dropped "
                    "connection and will retry forever",
                    scope.get("path"),
                )
            await send(message)

        await self.app(scope, receive, watched_send)
