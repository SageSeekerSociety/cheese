"""The browser task-machine fixture follows the connector device-flow contract."""

import httpx
import pytest

from scripts.e2e_task_machine import poll_approved_device


async def test_poll_waits_for_approval_after_a_pending_response():
    replies = iter(
        [
            {"status": "pending"},
            {"status": "approved", "token": "device-token", "device_id": "device-1"},
        ]
    )
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=next(replies))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://connector.test"
    ) as client:
        device = await poll_approved_device(client, "code-1", interval=0, timeout=1)

    assert device["token"] == "device-token"
    assert device["device_id"] == "device-1"
    assert len(requests) == 2
    assert all(
        request.url.path == "/connector/auth/device/poll" for request in requests
    )


@pytest.mark.parametrize("status", ["denied", "expired"])
async def test_poll_fails_on_terminal_status(status: str):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": status})
        ),
        base_url="http://connector.test",
    ) as client:
        with pytest.raises(RuntimeError, match=status):
            await poll_approved_device(client, "code-1", interval=0, timeout=1)


async def test_poll_stops_waiting_at_the_authorization_deadline():
    requests = []

    def still_pending(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "pending"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(still_pending), base_url="http://connector.test"
    ) as client:
        with pytest.raises(TimeoutError):
            await poll_approved_device(client, "code-1", interval=1, timeout=0.1)

    assert len(requests) == 1


async def test_poll_preserves_http_errors():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404)),
        base_url="http://connector.test",
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await poll_approved_device(client, "expired-code", interval=0, timeout=1)
