"""A runner that let its session go answers 「no such file」; whoever keeps
asking one is named in the log once per burst, so the next burst says who."""

import logging

import httpx
import pytest

from app.domain.agent import device_hub_rpc
from app.domain.agent.device_hub import DeviceCallError
from app.domain.agent.device_hub_rpc import RemoteDeviceHub


def _owner(message: str) -> httpx.MockTransport:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502, json={"error": {"name": "DeviceCallError", "message": message}}
        )

    return httpx.MockTransport(answer)


GONE = (
    "dial unix /tmp/cheese-execution-1000-ab.sock: connect: no such file or directory"
)


@pytest.fixture(autouse=True)
def _fresh_names():
    device_hub_rpc._gone_runner_named.clear()
    yield
    device_hub_rpc._gone_runner_named.clear()


async def _ask(hub: RemoteDeviceHub, state: str) -> None:
    with pytest.raises(DeviceCallError):
        await hub.call_executor("machine", state, "ping", {}, timeout=5)


def _named(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("gone runner asked")
    ]


@pytest.mark.anyio
async def test_a_burst_of_asks_names_its_asker_once(caplog) -> None:
    caplog.set_level(logging.INFO, logger=device_hub_rpc.__name__)
    hub = RemoteDeviceHub("http://owner", "secret", transport=_owner(GONE))
    for _ in range(3):
        await _ask(hub, "/state/one")
    await _ask(hub, "/state/two")

    named = _named(caplog)
    assert len(named) == 2
    assert "state=/state/one method=ping" in named[0]
    assert "test_a_burst_of_asks_names_its_asker_once" in named[0] or "_ask" in named[0]
    assert "state=/state/two" in named[1]


@pytest.mark.anyio
async def test_another_failure_is_not_read_as_a_gone_runner(caplog) -> None:
    caplog.set_level(logging.INFO, logger=device_hub_rpc.__name__)
    hub = RemoteDeviceHub(
        "http://owner", "secret", transport=_owner("permission denied")
    )
    await _ask(hub, "/state/one")
    assert _named(caplog) == []
