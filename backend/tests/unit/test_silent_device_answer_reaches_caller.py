"""What a backend call hears when the machine behind the owner never answers.

The owner gives up on a silent machine and says so with a 504, which the backend
reads as `TimeoutError` — the type every caller that copes with a silent machine
catches. On dev that answer never arrived: the backend stopped listening at the
same moment the owner stopped waiting, so every such call ended in a bare
`httpx.ReadTimeout` instead, and an agent asking for its machine got a 500.
"""

import asyncio

import httpx
import pytest

from app.core.config import settings
from app.device_connection_app import app as owner_app
from app.domain.agent.device_hub import device_hub as owner_hub
from app.domain.agent.device_hub_rpc import RemoteDeviceHub

DEVICE = "silent-machine"


class _SilentMachine:
    """Holds a link to the owner and never replies to anything sent over it."""

    async def send_json(self, msg: dict) -> None:
        return None

    async def send_bytes(self, data: bytes) -> None:
        return None


class _Network(httpx.AsyncBaseTransport):
    """The owner reached over a network: a short hop each way, and a read
    timeout that is enforced the way a socket enforces it."""

    def __init__(self) -> None:
        self._owner = httpx.ASGITransport(app=owner_app)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        read = request.extensions.get("timeout", {}).get("read")
        try:
            return await asyncio.wait_for(self._round_trip(request), read)
        except TimeoutError:
            raise httpx.ReadTimeout("timed out", request=request) from None

    async def _round_trip(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.02)
        response = await self._owner.handle_async_request(request)
        await asyncio.sleep(0.02)
        return response


@pytest.fixture
async def silent_machine(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "device_connection_secret", "test-owner-secret")
    link = _SilentMachine()
    await owner_hub.attach_device(DEVICE, link)
    yield DEVICE
    await owner_hub.detach_device(DEVICE, link)


@pytest.mark.anyio
async def test_a_machine_that_never_answers_is_reported_as_a_timeout(
    silent_machine: str,
) -> None:
    backend = RemoteDeviceHub(
        "http://owner", settings.device_connection_auth_secret, transport=_Network()
    )

    with pytest.raises(TimeoutError, match="did not answer exec"):
        await backend.exec(silent_machine, ["true"], timeout=0.1)
