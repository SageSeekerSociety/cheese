"""Streams the server opens to an executor and keeps open.

A call to an executor is one request and one answer (``calls``). A person's
terminal is neither: keystrokes go down while output comes up, for as long as
the person watches. ``open_executor_stream`` asks the machine's connector to dial the
executor of a state directory, write ``header`` as the first line — what the
stream is for, read by the executor — and relay bytes both ways from there
(``cli/internal/host/stream.go``). The hub ascribes them no meaning.

Output comes up on credit: the connector stops reading the executor once a
window of output is out unacknowledged, and reads on as the opener acks what
it passed on (``HubStream.ack``). So what waits in ``HubStream.output`` is at
most that window, and a flood waits on the machine rather than on the link
every session of the machine shares.

A stream lives on its link: when the link drops, every stream on it ends.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.domain.agent import device_link
from app.domain.agent.device_contract import DeviceOffline, DeviceUnreachable

if TYPE_CHECKING:
    from app.domain.agent import device_hub as pkg
    from app.domain.agent.device_hub.link import HubDevice


class StreamsUnsupported(Exception):
    """The machine's connector predates streams; it is told to update when it
    next says hello (``DeviceHub._update_if_stale``)."""


@dataclass
class HubStream:
    """One open stream, as its opener holds it."""

    device: HubDevice
    id: str
    #: What the executor wrote, chunk by chunk; ``None`` once the stream is
    #: over, on either side.
    output: asyncio.Queue[bytes | None] = field(default_factory=asyncio.Queue)
    #: Why the machine ended it, when it failed; empty when it closed cleanly.
    error: str = ""
    closed: bool = False

    async def send(self, data: bytes) -> None:
        if data and not self.closed:
            await self.device.send(device_link.stream_data(self.id, data))

    async def ack(self, taken: int) -> None:
        """``taken`` more bytes of output were passed on."""
        if taken > 0 and not self.closed:
            await self.device.send(device_link.stream_ack(self.id, taken))

    async def close(self) -> None:
        """The opener is done: the machine hangs up on the executor."""
        if self.device.open_streams.pop(self.id, None) is None:
            return
        self.end("")
        try:
            await self.device.send(device_link.stream_close(self.id))
        except DeviceOffline:
            pass

    def end(self, error: str) -> None:
        """The stream is over; ``error`` says why when it failed."""
        if self.closed:
            return
        self.closed, self.error = True, error
        self.output.put_nowait(None)


async def open_executor_stream(
    hub: pkg.DeviceHub, device_id: str, state: str, header: str
) -> HubStream:
    """Open a stream to the executor under ``state``. Raises
    ``DeviceUnreachable`` when the machine has no link, and
    ``StreamsUnsupported`` when its connector cannot open one."""
    device = hub._device(device_id)
    if not await hub._linked(device):
        raise DeviceUnreachable(device_id)
    if not device.streams:
        raise StreamsUnsupported(device_id)
    stream = HubStream(device=device, id="stream-" + uuid.uuid4().hex)
    device.open_streams[stream.id] = stream
    try:
        sent = await device.send(
            device_link.stream_open(stream.id, device.spelled(state), header)
        )
    except BaseException:
        device.open_streams.pop(stream.id, None)
        raise
    if not sent:
        device.open_streams.pop(stream.id, None)
        raise DeviceUnreachable(device_id)
    return stream


async def on_frame(
    hub: pkg.DeviceHub, device_id: str, msg: device_link.LinkMsg
) -> None:
    """One ``stream.*`` frame up from ``device_id``."""
    device = hub._device(device_id)
    stream = device.open_streams.get(msg.id)
    if msg.t == "stream.data":
        if stream is None:
            # Nobody here holds it (its opener went, or this process was
            # released while it was open): the machine stops relaying it.
            await device.send(device_link.stream_close(msg.id))
            return
        chunk = msg.decoded_data()
        if chunk:
            stream.output.put_nowait(chunk)
    elif msg.t == "stream.close" and stream is not None:
        device.open_streams.pop(msg.id, None)
        stream.end(msg.error)
