"""Exercise protocol ordering with an app-server peer, without a model account."""

import asyncio
import json

import pytest

from app.domain.agent.harness.codex import AppServer, AppServerError


class Writer:
    def __init__(self):
        self.messages = asyncio.Queue()

    def write(self, data):
        self.messages.put_nowait(json.loads(data))

    async def drain(self):
        pass


@pytest.mark.anyio
async def test_tool_wait_does_not_block_interrupt_and_events():
    reader, writer = asyncio.StreamReader(), Writer()
    events = []
    release = asyncio.Event()

    async def on_event(event):
        events.append(event)

    async def tool(method, params):
        assert method == "item/tool/call"
        await release.wait()
        return {"success": True, "contentItems": []}

    client = AppServer(reader, writer, on_event=on_event, on_request=tool)
    listener = asyncio.create_task(client.listen())

    def receive(message):
        reader.feed_data(json.dumps(message).encode() + b"\n")

    try:
        initialized = asyncio.create_task(client.initialize())
        request = await writer.messages.get()
        assert request["method"] == "initialize"
        receive({"id": request["id"], "result": {"userAgent": "fixture"}})
        assert await initialized == {"userAgent": "fixture"}
        assert await writer.messages.get() == {"method": "initialized"}
        receive({"id": "tool-1", "method": "item/tool/call", "params": {}})
        interrupt = asyncio.create_task(
            client.request("turn/interrupt", {"turnId": "t"})
        )
        request = await writer.messages.get()
        receive({"id": request["id"], "result": {}})
        receive({"method": "turn/completed", "params": {"status": "interrupted"}})
        assert await asyncio.wait_for(interrupt, 1) == {}
        assert events[-1]["method"] == "turn/completed"
        release.set()
        reply = await writer.messages.get()
        assert reply["id"] == "tool-1"
        assert reply["result"]["success"] is True
    finally:
        reader.feed_eof()
        await listener


@pytest.mark.anyio
async def test_rejected_request_and_disconnect_are_not_success():
    reader, writer = asyncio.StreamReader(), Writer()

    async def callback(*args):
        return {}

    client = AppServer(reader, writer, on_event=callback, on_request=callback)
    listener = asyncio.create_task(client.listen())
    request = asyncio.create_task(
        client.request("thread/resume", {"threadId": "missing"})
    )
    sent = await writer.messages.get()
    reader.feed_data(
        json.dumps(
            {"id": sent["id"], "error": {"code": -1, "message": "missing thread"}}
        ).encode()
        + b"\n"
    )
    with pytest.raises(AppServerError, match="missing thread"):
        await request
    waiting = asyncio.create_task(client.request("turn/start", {}))
    await writer.messages.get()
    reader.feed_eof()
    with pytest.raises(ConnectionError, match="disconnected"):
        await waiting
    await listener
