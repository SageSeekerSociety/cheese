"""The device read view must not freeze because one poll came back wrong.

`_refresh_loop` used to catch only HTTPError and OSError. Anything else ended
the loop for the life of the process, and nothing observes that task — it is
created bare and awaited only by `close()` under `return_exceptions=True`. The
backend then kept answering `is_online` from a snapshot that had stopped
updating, and the screen/device teardown `refresh` drives stopped with it.
"""

import asyncio

import httpx
import pytest

from app.domain.agent.device_hub_rpc import RemoteDeviceHub

GOOD = {
    "devices": [{"device_id": "machine-1", "online": True}],
    "screens": [],
}


@pytest.mark.anyio
async def test_a_body_it_cannot_parse_does_not_end_the_poller() -> None:
    """HTML from a proxy is the realistic case; it raises inside response.json(),
    which is neither an HTTPError nor an OSError."""
    answers = ["<html>502 Bad Gateway</html>", None]

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal answers
        if answers and answers[0] is not None:
            return httpx.Response(200, text=answers.pop(0))
        return httpx.Response(200, json=GOOD)

    hub = RemoteDeviceHub("http://owner", "secret")
    hub._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://owner"
    )
    # The first refresh is the bad one; it must not propagate out of the loop.
    hub._refresh_task = asyncio.create_task(hub._refresh_loop())
    try:
        await asyncio.sleep(2.5)
        assert not hub._refresh_task.done(), "the poller ended on a bad body"
        assert hub.is_online("machine-1"), "the poller never recovered"
    finally:
        await hub.close()


@pytest.mark.anyio
async def test_a_transport_error_is_still_the_quiet_path() -> None:
    """The expected transient case keeps its existing silence, so a rollout does
    not fill the log with one line a second."""
    state = {"fail": True}

    def handler(_request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectError("owner restarting")
        return httpx.Response(200, json=GOOD)

    hub = RemoteDeviceHub("http://owner", "secret")
    hub._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://owner"
    )
    hub._refresh_task = asyncio.create_task(hub._refresh_loop())
    try:
        await asyncio.sleep(1.5)
        assert not hub._refresh_task.done()
        state["fail"] = False
        await asyncio.sleep(1.5)
        assert hub.is_online("machine-1")
    finally:
        await hub.close()
