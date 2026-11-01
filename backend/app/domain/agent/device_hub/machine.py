"""Streams a session's sandbox opens on its platform socket.

The sandbox holds no credential of its own. What it asks of the platform it
asks on one socket the connector serves outside it, in a directory the sandbox
is shown read-only (``confinement.sandbox_argv``); the connector relays each
connection there as a ``machine.*`` stream tagged with the state directory it
arrived on (``cli/internal/host/machine.go``). That tag, on this device's own
link, is the whole of the caller's identity: no frame of the stream can change
it, and the request inside carries nothing that could.

Each stream holds one HTTP/1.1 request. It is read here until it is complete,
handed to the handler the process that owns this hub serves it with
(``serve``), and the handler's bytes go back down until it returns; then the
stream is closed. Nothing is served before a handler is set: a stream then gets
a 503, said by this module, so the sandbox hears why.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import h11

from app.domain.agent import device_link

if TYPE_CHECKING:
    from app.domain.agent import device_hub as pkg
    from app.domain.agent.device_hub.link import HubDevice

logger = logging.getLogger("app.domain.agent.device_hub")

#: A request bigger than this is refused. The largest body a sandbox sends
#: through this socket is a Git push relayed to the forge (`forge_token.py`),
#: so the ceiling leaves room for a repository object rather than the small
#: JSON calls the CLI usually makes; the extra 64 KiB covers the HTTP head.
MAX_REQUEST_BYTES = 512 * 1024 * 1024 + 64 * 1024
#: Bodies past this size stream to the handler as frames arrive instead of
#: waiting in memory for the end of the request.
_STREAM_THRESHOLD = 64 * 1024


@dataclass(frozen=True)
class MachineRequest:
    """One request a sandbox sent, and whose it is."""

    device_id: str
    state: str
    method: str
    target: str
    headers: list[tuple[str, str]]
    body: bytes = b""
    chunks: AsyncIterator[bytes] | None = None

    async def stream(self) -> AsyncIterator[bytes]:
        if self.chunks is not None:
            async for chunk in self.chunks:
                yield chunk
        elif self.body:
            yield self.body


Write = Callable[[bytes], Awaitable[None]]
Handler = Callable[[MachineRequest, Write], Awaitable[None]]

_handler: Handler | None = None


def serve(handler: Handler | None) -> None:
    """Set what this process answers machine streams with."""
    global _handler
    _handler = handler


@dataclass
class MachineStream:
    """One stream's request as it arrives."""

    state: str
    parser: h11.Connection = field(default_factory=lambda: h11.Connection(h11.SERVER))
    received: int = 0
    method: str = ""
    target: str = ""
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytearray = field(default_factory=bytearray)
    queue: asyncio.Queue[bytes | Exception | None] | None = None
    ended: bool = False
    task: asyncio.Task[None] | None = None


def plain_answer(status: int, text: str) -> bytes:
    """A whole HTTP/1.1 answer this side writes itself."""
    body = (text + "\n").encode()
    reason = {400: "Bad Request", 413: "Content Too Large", 503: "Unavailable"}
    return (
        f"HTTP/1.1 {status} {reason.get(status, 'Error')}\r\n"
        "content-type: text/plain; charset=utf-8\r\n"
        f"content-length: {len(body)}\r\nconnection: close\r\n\r\n"
    ).encode() + body


async def on_frame(hub: pkg.DeviceHub, device_id: str, msg: device_link.LinkMsg):
    """One ``machine.*`` frame up from ``device_id``."""
    device = hub._device(device_id)
    if msg.t == "machine.open":
        if msg.path:
            device.machine_streams[msg.id] = MachineStream(state=msg.path)
        return
    stream = device.machine_streams.get(msg.id)
    if stream is None or stream.ended:
        if msg.t == "machine.close":
            device.machine_streams.pop(msg.id, None)
        return
    if msg.t == "machine.close":
        device.machine_streams.pop(msg.id, None)
        if stream.queue is not None:
            stream.queue.put_nowait(_Malformed())
        return
    if msg.t == "machine.data":
        chunk = msg.decoded_data()
        stream.received += len(chunk)
        if stream.received > MAX_REQUEST_BYTES:
            if stream.queue is not None:
                stream.queue.put_nowait(_Malformed())
            await _refuse(device, msg.id, 413, "request too large")
            return
        stream.parser.receive_data(chunk)
    elif msg.t == "machine.end":
        stream.parser.receive_data(b"")
    else:
        return
    try:
        request = _complete(stream, device_id)
    except _TooLarge:
        if stream.queue is not None:
            stream.queue.put_nowait(_Malformed())
        await _refuse(device, msg.id, 413, "request too large")
        return
    except _Malformed:
        if stream.queue is not None:
            stream.queue.put_nowait(_Malformed())
        await _refuse(device, msg.id, 400, "not an HTTP/1.1 request")
        return
    if request is not None and stream.task is None:
        stream.task = asyncio.create_task(_answer(device, msg.id, request))


class _Malformed(Exception):
    """What the sandbox sent is not one HTTP/1.1 request."""


class _TooLarge(Exception):
    """The declared Content-Length exceeds MAX_REQUEST_BYTES."""


async def _iter_queue(
    queue: asyncio.Queue[bytes | Exception | None],
) -> AsyncIterator[bytes]:
    while True:
        item = await queue.get()
        if item is None:
            return
        if isinstance(item, Exception):
            raise item
        yield item


def _complete(stream: MachineStream, device_id: str) -> MachineRequest | None:
    """The request once all of it is in, or once its body begins streaming."""
    while True:
        try:
            event = stream.parser.next_event()
        except h11.RemoteProtocolError as exc:
            raise _Malformed from exc
        if event is h11.NEED_DATA:
            break
        if isinstance(event, h11.Request):
            # Latin-1 decodes any byte: a header value with obs-text is the
            # sandbox's own business, never a reason to lose the link this
            # read loop serves for every session on the machine.
            stream.method = event.method.decode("latin-1")
            stream.target = event.target.decode("latin-1")
            stream.headers = [
                (k.decode("latin-1"), v.decode("latin-1")) for k, v in event.headers
            ]
            for name, value in stream.headers:
                if name.lower() == "content-length" and value.strip().isdigit():
                    if int(value.strip()) > MAX_REQUEST_BYTES:
                        raise _TooLarge
        elif isinstance(event, h11.Data):
            if stream.queue is not None:
                stream.queue.put_nowait(bytes(event.data))
            else:
                stream.body.extend(event.data)
        elif isinstance(event, h11.EndOfMessage):
            stream.ended = True
            if stream.queue is not None:
                stream.queue.put_nowait(None)
            elif stream.task is None:
                return MachineRequest(
                    device_id,
                    stream.state,
                    stream.method,
                    stream.target,
                    stream.headers,
                    bytes(stream.body),
                )
            return None
        else:  # the sandbox hung up before its request was whole
            raise _Malformed
    if (
        stream.task is None
        and stream.method
        and not stream.ended
        and len(stream.body) >= _STREAM_THRESHOLD
    ):
        queue: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()
        if stream.body:
            queue.put_nowait(bytes(stream.body))
            stream.body.clear()
        stream.queue = queue
        return MachineRequest(
            device_id,
            stream.state,
            stream.method,
            stream.target,
            stream.headers,
            chunks=_iter_queue(queue),
        )
    return None


async def _answer(device: HubDevice, stream_id: str, request: MachineRequest) -> None:
    async def write(data: bytes) -> None:
        if data:
            await device.send(device_link.machine_data(stream_id, data))

    try:
        if _handler is None:
            await write(plain_answer(503, "this platform serves no machine requests"))
        else:
            await _handler(request, write)
    except Exception:  # the stream ends either way; the reason stays here
        logger.exception("machine stream %s failed", stream_id)
    finally:
        device.machine_streams.pop(stream_id, None)
        await device.send(device_link.machine_close(stream_id))


async def _refuse(device: HubDevice, stream_id: str, status: int, text: str) -> None:
    device.machine_streams.pop(stream_id, None)
    await device.send(device_link.machine_data(stream_id, plain_answer(status, text)))
    await device.send(device_link.machine_close(stream_id))
