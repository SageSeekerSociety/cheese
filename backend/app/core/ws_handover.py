"""The browsers' sockets leave with the running work.

A rollout switches traffic to the next backend, but a socket a browser already
holds stays on this process until app-router's old workers close it, about 37 s
after the switch on dev. Once this process has handed the running work over
(the lifespan's `hand_over`, at the switch) it publishes no more room frames,
so for the rest of that time the browser is connected to a process with nothing
to say. Ending those sockets at the handover, with 1012 (service restart), sends
each browser back through app-router to the backend that now runs the work.
The client reconnects on any close it did not ask for and re-reads what it
missed (`useRoomSocket.ts`).

Only the sockets app-router carries are ended. Device, screen, model and forge
connections reach this process through the standing ingress and outlive the
handover on purpose.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Awaitable, Callable
from typing import Any

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

SERVICE_RESTART = 1012

BUSINESS_SOCKETS = re.compile(r"^/(?:topics/[^/]+/chat|notifications/live)$")


class _Socket:
    def __init__(self, send: Send) -> None:
        self.send = send
        # Starlette must not send on one socket from two tasks at once, and the
        # handover sends from its own; every send on it goes through this lock.
        self.lock = asyncio.Lock()
        self.ended = asyncio.Event()
        self.closed = False


class BusinessSockets:
    """The open sockets of this process that the handover ends."""

    def __init__(self) -> None:
        self._open: set[_Socket] = set()

    async def end_all(self) -> None:
        for socket in list(self._open):
            async with socket.lock:
                if not socket.closed:
                    socket.closed = True
                    # Already gone from the browser's side: nothing to tell it.
                    with contextlib.suppress(Exception):
                        await socket.send(
                            {"type": "websocket.close", "code": SERVICE_RESTART}
                        )
            socket.ended.set()


business_sockets = BusinessSockets()


class EndBusinessSocketsAtHandover:
    def __init__(self, app: Any, sockets: BusinessSockets = business_sockets) -> None:
        self.app = app
        self.sockets = sockets

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "websocket" or not BUSINESS_SOCKETS.match(
            scope.get("path", "")
        ):
            await self.app(scope, receive, send)
            return

        socket = _Socket(send)
        ended = {"type": "websocket.disconnect", "code": SERVICE_RESTART}

        async def guarded_send(message: Message) -> None:
            async with socket.lock:
                if socket.closed:
                    return
                if message.get("type") == "websocket.close":
                    socket.closed = True
                await send(message)

        async def guarded_receive() -> Message:
            if socket.ended.is_set():
                return ended
            incoming = asyncio.ensure_future(receive())
            handed_over = asyncio.ensure_future(socket.ended.wait())
            await asyncio.wait(
                {incoming, handed_over}, return_when=asyncio.FIRST_COMPLETED
            )
            handed_over.cancel()
            if incoming.done():
                return incoming.result()
            incoming.cancel()
            # The handler leaves the way it does when the browser goes.
            return ended

        self.sockets._open.add(socket)
        try:
            await self.app(scope, guarded_receive, guarded_send)
        finally:
            self.sockets._open.discard(socket)
