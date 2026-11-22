"""A stream the server holds open to an executor, through the device hub.

What the person's terminal stands on: bytes go down and come up for as long
as the stream is open, its end is heard whichever side ends it, and a machine
whose connector cannot open one says so instead of leaving the opener waiting.
"""

import asyncio
import base64

import pytest

from app.domain.agent.device_hub import DeviceHub
from app.domain.agent.device_hub.streams import StreamsUnsupported
from tests.support import wire


async def _linked(streams: bool) -> tuple[DeviceHub, wire.RecordingDevice]:
    hub = DeviceHub()
    device = wire.RecordingDevice()
    await hub.attach_device("dev", device)
    await hub.on_device_message(
        "dev", {"t": "hello", "v": 1, "executor": True, "streams": streams}
    )
    return hub, device


async def _next(device: wire.RecordingDevice, kind: str) -> dict:
    while True:
        frame = await asyncio.wait_for(device.sent.get(), 5)
        if frame["t"] == kind:
            return frame


def _up(stream_id: str, payload: bytes) -> dict:
    return {
        "t": "stream.data",
        "id": stream_id,
        "data": base64.b64encode(payload).decode(),
    }


async def test_bytes_go_both_ways_until_the_machine_ends_the_stream() -> None:
    hub, device = await _linked(streams=True)
    stream = await hub.open_executor_stream(
        "dev", "$HOME/state", '{"method":"terminal"}'
    )
    opened = await _next(device, "stream.open")
    assert (opened["path"], opened["header"]) == (
        "$HOME/state",
        '{"method":"terminal"}',
    )

    await hub.on_device_message("dev", _up(opened["id"], b"prompt$ "))
    assert await asyncio.wait_for(stream.output.get(), 5) == b"prompt$ "

    await stream.send(b"ls\n")
    down = await _next(device, "stream.data")
    assert base64.b64decode(down["data"]) == b"ls\n"
    await stream.ack(8)
    assert (await _next(device, "stream.ack"))["bytes"] == 8

    await hub.on_device_message(
        "dev", {"t": "stream.close", "id": opened["id"], "error": "gone"}
    )
    assert await asyncio.wait_for(stream.output.get(), 5) is None
    assert stream.error == "gone"


async def test_a_connector_that_cannot_open_a_stream_says_so() -> None:
    hub, _ = await _linked(streams=False)
    with pytest.raises(StreamsUnsupported):
        await hub.open_executor_stream("dev", "$HOME/state", "{}")


async def test_a_lost_link_ends_every_stream_on_it() -> None:
    hub, device = await _linked(streams=True)
    stream = await hub.open_executor_stream("dev", "$HOME/state", "{}")
    await hub.detach_device("dev", device)
    assert await asyncio.wait_for(stream.output.get(), 5) is None
    assert stream.error


async def test_output_of_a_stream_nobody_holds_is_stopped_at_the_machine() -> None:
    hub, device = await _linked(streams=True)
    await hub.on_device_message("dev", _up("stream-orphan", b"still here"))
    assert (await _next(device, "stream.close"))["id"] == "stream-orphan"


async def test_closing_a_stream_hangs_up_on_the_machine() -> None:
    hub, device = await _linked(streams=True)
    stream = await hub.open_executor_stream("dev", "$HOME/state", "{}")
    opened = await _next(device, "stream.open")
    await stream.close()
    assert (await _next(device, "stream.close"))["id"] == opened["id"]
    assert await asyncio.wait_for(stream.output.get(), 5) is None
