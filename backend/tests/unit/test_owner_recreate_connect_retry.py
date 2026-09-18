"""What a backend call does while the connection owner is being recreated.

Releasing the owner replaces its container, and for those seconds nothing is
listening on its port. Every call into it then failed outright — on 2026-09-17
a room's control poll turned that window into six 500s on the page, one per
poll, while the room itself was perfectly readable.
"""

import httpx
import pytest

from app.domain.agent import device_hub_rpc
from app.domain.agent.device_hub_rpc import RemoteDeviceHub


class _OwnerComingBack(httpx.AsyncBaseTransport):
    """An owner that refuses the first `refusals` connects, then answers."""

    def __init__(self, refusals: int, result: object = "ok") -> None:
        self.refusals = refusals
        self.result = result
        self.attempts = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.attempts += 1
        if self.attempts <= self.refusals:
            raise httpx.ConnectError("All connection attempts failed", request=request)
        return httpx.Response(200, json={"result": self.result})


@pytest.fixture(autouse=True)
def _quick_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the waits short enough to run; the window itself stays as shipped."""
    monkeypatch.setattr(device_hub_rpc, "OWNER_CONNECT_RETRY_MAX_DELAY_S", 0.01)


@pytest.mark.anyio
async def test_a_call_waits_for_the_owner_to_come_back() -> None:
    transport = _OwnerComingBack(refusals=3, result={"exit": 0})
    hub = RemoteDeviceHub("http://owner", "test-owner-secret", transport=transport)

    assert await hub.exec("machine-7", ["true"]) == {"exit": 0}
    assert transport.attempts == 4


@pytest.mark.anyio
async def test_an_owner_that_never_comes_back_still_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The release gives the new container 60 seconds; past that the call is
    reporting a broken release, and saying so beats hanging on it."""
    monkeypatch.setattr(device_hub_rpc, "OWNER_CONNECT_RETRY_WINDOW_S", 0.05)
    transport = _OwnerComingBack(refusals=1000)
    hub = RemoteDeviceHub("http://owner", "test-owner-secret", transport=transport)

    with pytest.raises(httpx.ConnectError):
        await hub.exec("machine-7", ["true"])


@pytest.mark.anyio
async def test_the_snapshot_read_does_not_wait() -> None:
    """`start()` awaits one of these in the lifespan. A backend booting while the
    owner is down must not hold its own startup here — its health check would
    fail and the deploy would roll it back over a condition the next poll clears.
    """
    transport = _OwnerComingBack(refusals=1)
    hub = RemoteDeviceHub("http://owner", "test-owner-secret", transport=transport)

    with pytest.raises(httpx.ConnectError):
        await hub.refresh()
    assert transport.attempts == 1
