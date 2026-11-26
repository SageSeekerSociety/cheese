"""A device call's JSON body is encoded off the event loop.

`exec` carries a launcher script: a launch's payload is 472 KiB of script and
649 KiB of JSON once encoded. httpx encodes `json=` inside the request it
builds, on the loop; production's stall board has a 108 ms stall whose whole
stack is that encode (`httpx/_content.py encode_json`).

The bytes are unchanged, which is what these tests are about: the same encoder,
the same content type, on a thread instead of the loop.
"""

import asyncio

import httpx
import pytest

from app.domain.agent.device_hub_rpc import _json_body


def test_the_bytes_are_the_ones_httpx_would_have_built():
    payload = {"a": "中文", "n": 1, "x": [1.5, None, True], "s": 'quote " here'}

    assert (
        _json_body(payload)
        == httpx.Request("POST", "https://owner/", json=payload).content
    )


async def test_a_call_encodes_its_body_on_a_thread(monkeypatch):
    """The claim is about the call site, not about `_json_body`: a slow encode
    must not hold the loop, so the encode itself has to run off it."""
    from app.domain.agent import device_hub_rpc

    beats: list[float] = []
    done = False

    async def beat():
        loop = asyncio.get_running_loop()
        while not done:
            started = loop.time()
            await asyncio.sleep(0.01)
            beats.append(loop.time() - started - 0.01)

    def slow_encode(_payload):
        import time

        time.sleep(0.15)
        return b"{}"

    monkeypatch.setattr(device_hub_rpc, "_json_body", slow_encode)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/snapshot"):
            return httpx.Response(200, json={"devices": [], "screens": []})
        return httpx.Response(200, json={"result": "ok"})

    hub = device_hub_rpc.RemoteDeviceHub(
        "http://owner", "secret", transport=httpx.MockTransport(handler)
    )
    await hub.start()
    task = asyncio.create_task(beat())
    await asyncio.sleep(0)
    try:
        await hub._call("exec", {"device_id": "d", "argv": ["true"]})
    finally:
        done = True
        await task
        await hub.close()

    assert beats, "the beater never ran"
    assert max(beats) < 0.05, f"the loop was held for {max(beats) * 1000:.0f} ms"


@pytest.mark.anyio
async def test_a_call_still_reaches_the_owner(monkeypatch):
    """The header and the bytes go out together; a body without the JSON
    content type is a 400 the owner answers before it looks at anything."""
    from app.domain.agent import device_hub_rpc

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as jsonlib

        if request.url.path.endswith("/snapshot"):
            # `start()` reads one before it serves any call.
            return httpx.Response(200, json={"devices": [], "screens": []})
        seen["content_type"] = request.headers.get("content-type")
        seen["body"] = jsonlib.loads(request.content)
        return httpx.Response(200, json={"result": "ok"})

    hub = device_hub_rpc.RemoteDeviceHub(
        "http://owner", "secret", transport=httpx.MockTransport(handler)
    )
    await hub.start()
    try:
        result = await hub._call("exec", {"device_id": "d", "argv": ["true"]})
    finally:
        await hub.close()

    assert result == "ok"
    assert seen["content_type"] == "application/json"
    assert seen["body"]["device_id"] == "d"
