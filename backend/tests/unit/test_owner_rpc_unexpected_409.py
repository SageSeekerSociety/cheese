"""What the backend does with a 409 the connection owner did not send.

Only the owner's 「device offline」 names a device, in `X-Device-Id`. Reading
that name out of a 409 that did not carry it raised KeyError from inside the
transport, which then surfaced as whatever the caller happened to catch — on
2026-09-16, as `pi entry read failed` on a poller and as a 500 on
/topics/{id}/agent/control. Neither named a 409, and the body that would have
said what sent it was lost.
"""

import httpx
import pytest

from app.domain.agent.device_hub import DeviceOffline
from app.domain.agent.device_hub_rpc import RemoteDeviceHub


def _hub_answering(response: httpx.Response) -> RemoteDeviceHub:
    """A hub whose owner answers with exactly this response."""
    hub = RemoteDeviceHub("http://owner", "test-owner-secret")
    hub._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: response),
        base_url="http://owner",
    )
    return hub


@pytest.mark.anyio
async def test_the_owners_409_still_names_the_device_that_went_offline() -> None:
    hub = _hub_answering(
        httpx.Response(
            409, json={"detail": "device offline"}, headers={"X-Device-Id": "machine-7"}
        )
    )
    with pytest.raises(DeviceOffline) as raised:
        await hub._request("POST", "/internal/device-connection/call/exec")
    assert raised.value.device_id == "machine-7"


@pytest.mark.anyio
async def test_a_409_from_anywhere_else_says_what_it_was() -> None:
    """The failure must name the response, not a missing dictionary key."""
    hub = _hub_answering(httpx.Response(409, text="device calls are active"))
    with pytest.raises(httpx.HTTPStatusError) as raised:
        await hub._request("POST", "/internal/device-connection/call/exec")
    assert raised.value.response.status_code == 409
    assert "device calls are active" in raised.value.response.text


@pytest.mark.anyio
async def test_a_409_from_anywhere_else_is_not_a_key_error() -> None:
    """Pinned separately because KeyError is what it used to be, and a KeyError
    tells whoever catches it nothing about a 409 having happened at all."""
    hub = _hub_answering(httpx.Response(409, text="something else entirely"))
    with pytest.raises(Exception) as raised:  # noqa: PT011 — the point is the type
        await hub._request("GET", "/internal/device-connection/snapshot")
    assert not isinstance(raised.value, KeyError)
