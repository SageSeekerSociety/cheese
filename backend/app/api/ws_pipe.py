"""The byte pipe the two machine-facing tunnels share.

`routes/llm_tunnel.py` (a subscription turn's way to the metering proxy) and
`routes/forge_token.py` (a forge tunnel) both relay one TCP connection over one
WebSocket, in both directions at once, and in neither is one direction ending
alone allowed to leave the other hanging. `_Activity` is how the relay says it
is still carrying something — the idle cut reads it.

It lives here because two route modules pump bytes through it and a route
module does not import another one (C4 `routes-independent`). Nothing here
reads `app`: the pipe is as dumb as the tunnels it serves, and never parses the
bytes it moves.
"""

import asyncio
import time

from fastapi import WebSocket

# One read; the pipe is latency-sensitive and a turn's frames are small.
_CHUNK = 65536


class _Activity:
    def __init__(self) -> None:
        self.last = time.monotonic()

    def touch(self) -> None:
        self.last = time.monotonic()


async def _pump_ws_to_tcp(
    ws: WebSocket, writer: asyncio.StreamWriter, activity: _Activity | None = None
) -> None:
    while True:
        data = await ws.receive_bytes()
        if activity is not None:
            activity.touch()
        writer.write(data)
        await writer.drain()


async def _pump_tcp_to_ws(
    reader: asyncio.StreamReader, ws: WebSocket, activity: _Activity | None = None
) -> None:
    while True:
        data = await reader.read(_CHUNK)
        if not data:
            return  # the far side closed; ending this side ends the pair
        if activity is not None:
            activity.touch()
        await ws.send_bytes(data)
