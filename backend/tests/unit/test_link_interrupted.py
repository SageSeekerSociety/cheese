"""A call whose link drops under it has an unknown outcome, not a lost machine.

The frame had already left: the machine may have run it. What reaches the
caller must say exactly that — neither "the machine is out of reach" (it is
usually back within seconds) nor "retry it" (that could do it twice) — across
every hop between the machine and the agent."""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.device_connection_app import app as owner_app
from app.domain.agent import device_hub, executor_transport
from app.domain.agent.device_hub import DeviceOffline, LinkInterrupted
from app.domain.agent.device_hub import device_hub as owner_hub
from app.domain.agent.device_hub_rpc import RemoteDeviceHub

DEVICE = "blinking-machine"


class Link:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)

    async def send_bytes(self, data: bytes) -> None:
        return None


@pytest.fixture
async def backend(monkeypatch):
    monkeypatch.setattr(settings, "device_connection_secret", "test-owner-secret")
    monkeypatch.setattr(device_hub, "RECONNECT_GRACE_S", 0.1)
    hub = RemoteDeviceHub(
        "http://owner",
        settings.device_connection_auth_secret,
        transport=httpx.ASGITransport(app=owner_app),
    )
    yield hub
    owner_hub._devices.pop(DEVICE, None)


@pytest.mark.anyio
async def test_a_command_cut_off_by_the_link_reaches_the_backend_as_interrupted(
    backend,
):
    link = Link()
    await owner_hub.attach_device(DEVICE, link)
    command = asyncio.create_task(backend.exec(DEVICE, ["touch", "x"], timeout=5))
    for _ in range(100):
        if any(m["t"] == "exec" for m in link.sent):
            break
        await asyncio.sleep(0.01)
    await owner_hub.detach_device(DEVICE, link)

    with pytest.raises(LinkInterrupted):
        await asyncio.wait_for(command, 2)


@pytest.mark.anyio
async def test_a_machine_that_stays_away_reaches_the_backend_as_offline(backend):
    link = Link()
    await owner_hub.attach_device(DEVICE, link)
    await owner_hub.detach_device(DEVICE, link)

    with pytest.raises(DeviceOffline) as raised:
        await asyncio.wait_for(backend.exec(DEVICE, ["touch", "x"], timeout=5), 2)
    assert not isinstance(raised.value, LinkInterrupted)
    assert [m["t"] for m in link.sent] == ["welcome"]


async def _platform_answer(exc: Exception) -> httpx.Response:
    """What the platform's execution route answers when the hub raises ``exc``."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/execution")
    async def execution():
        raise exc

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://platform"
    ) as client:
        return await client.post("/execution")


def _client_hearing(monkeypatch, tmp_path, answer: httpx.Response):
    """The executor client on the machine, receiving ``answer``."""
    token = tmp_path / "execution.token"
    token.write_text("t")

    class Response:
        status = answer.status_code

        @staticmethod
        def read():
            return answer.content

        @staticmethod
        def getheader(name):
            return answer.headers.get(name)

    class Connection:
        sock = None

        def request(self, method, path, *, body, headers):
            return None

        @staticmethod
        def getresponse():
            return Response()

        @staticmethod
        def close():
            pass

    client = executor_transport.RemoteClient(
        {"kind": "device", "url": "http://executor.test", "token_file": str(token)}
    )
    monkeypatch.setattr(client, "connection", lambda: (Connection(), "/execution"))
    client.transport.headers = {}
    return client


async def test_the_agent_is_told_the_outcome_is_unknown(monkeypatch, tmp_path):
    answer = await _platform_answer(LinkInterrupted("abcd1234"))
    client = _client_hearing(monkeypatch, tmp_path, answer)

    with pytest.raises(RuntimeError) as raised:
        client.call("invoke")

    assert not isinstance(raised.value, executor_transport.MachineOutOfReach)
    assert str(raised.value) == executor_transport.LINK_INTERRUPTED


async def test_a_person_reads_the_interruption_in_their_own_language():
    body = (await _platform_answer(LinkInterrupted("abcd1234"))).json()
    assert body["error"]["i18n"] == {
        "key": "deviceLinkInterrupted",
        "params": {"device": "abcd1234"},
    }


async def test_a_machine_that_is_gone_is_still_out_of_reach(monkeypatch, tmp_path):
    answer = await _platform_answer(DeviceOffline("abcd1234"))
    client = _client_hearing(monkeypatch, tmp_path, answer)

    with pytest.raises(executor_transport.MachineOutOfReach):
        client.call("invoke")
