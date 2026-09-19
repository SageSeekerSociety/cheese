"""What travels when the machine answers a call with a failure of its own.

「dial unix …sock: no such file」 is the connector saying the runner's socket is
not there yet; 「lstat …/.cheese/executor: no such file」 that the home it was
asked about is gone. The hub raised them as bare RuntimeError, which the owner's
catch-all turned into 500「服务器内部错误」, which the backend's transport turned
into `HTTPStatusError: Server error '500' for url …/call/call_executor`, which
the backend's catch-all turned into a second 500 — two alerts describing this
server for every answer the machine sent, and the machine's own words gone
from the one that reached the room (17 alerts folding 29 repeats, 2026-09-18).

These pin the boundary: the owner answers 502 carrying the words, and the
backend's transport hands its caller the same exception the in-process hub
raises, so a caller reads one type on either side of the owner.
"""

from typing import Any

import httpx
import pytest

from app import device_connection_app
from app.core.config import settings
from app.domain.agent.device_hub import DeviceCallError, device_hub
from app.domain.agent.device_hub_rpc import RemoteDeviceHub

THE_MACHINES_WORDS = (
    "dial unix /tmp/cheese-execution-1000-2d8dc1e0d56aee2d82c7dfee.sock: "
    "connect: no such file or directory"
)


@pytest.fixture(autouse=True)
def owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "device_connection_owner", True)
    monkeypatch.setattr(settings, "device_connection_secret", "test-owner-secret")
    monkeypatch.setattr(device_connection_app, "_release_draining", False)
    monkeypatch.setattr(device_connection_app, "_active_rpc_calls", 0)
    monkeypatch.setattr(device_connection_app, "_executor_calls", {})


def _owner_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=device_connection_app.app),
        base_url="http://owner",
        headers={"X-Device-Connection-Secret": "test-owner-secret"},
    )


async def _call_executor() -> httpx.Response:
    async with _owner_client() as client:
        return await client.post(
            "/internal/device-connection/call/call_executor",
            json={
                "device_id": "machine-7",
                "state": "/state",
                "method": "read",
                "params": {},
                "trace_id": "trace-1",
            },
        )


@pytest.mark.anyio
async def test_the_machines_failure_is_relayed_with_its_words_not_as_an_owner_fault(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def the_machine_says_no(*_args: Any, **_kwargs: Any) -> dict:
        raise DeviceCallError(THE_MACHINES_WORDS)

    monkeypatch.setattr(device_hub, "call_executor", the_machine_says_no)
    caplog.set_level("ERROR")

    response = await _call_executor()

    assert response.status_code == 502
    assert response.json()["error"] == {
        "name": "DeviceCallError",
        "message": THE_MACHINES_WORDS,
        "data": None,
    }
    # The whole point: nothing at ERROR, so nothing for the alert channel.
    assert [r.message for r in caplog.records if r.levelname == "ERROR"] == []


@pytest.mark.anyio
async def test_the_backend_reads_the_relayed_failure_as_the_hubs_exception() -> None:
    hub = RemoteDeviceHub("http://owner", "test-owner-secret")
    hub._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=device_connection_app.app),
        base_url="http://owner",
        headers={"X-Device-Connection-Secret": "test-owner-secret"},
    )

    async def the_machine_says_no(*_args: Any, **_kwargs: Any) -> dict:
        raise DeviceCallError(THE_MACHINES_WORDS)

    device_hub_call_executor = device_hub.call_executor
    device_hub.call_executor = the_machine_says_no  # type: ignore[method-assign]
    try:
        with pytest.raises(DeviceCallError, match="no such file or directory"):
            await hub.call_executor("machine-7", "/state", "read", {})
    finally:
        device_hub.call_executor = device_hub_call_executor  # type: ignore[method-assign]


@pytest.mark.anyio
async def test_a_502_that_is_not_the_machines_answer_keeps_status_and_body() -> None:
    hub = RemoteDeviceHub("http://owner", "test-owner-secret")
    hub._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(502, text="<html>bad gateway</html>")
        ),
        base_url="http://owner",
    )
    with pytest.raises(httpx.HTTPStatusError) as raised:
        await hub._request("POST", "/internal/device-connection/call/exec")
    assert raised.value.response.status_code == 502
    assert "bad gateway" in raised.value.response.text
