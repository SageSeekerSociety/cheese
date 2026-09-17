"""What the connection owner answers when a device never replies.

The owner holds the device WebSockets across backend releases, and a business
backend reaches it over HTTP. ``device_hub.exec`` waits ``timeout + 5`` on the
device's reply and then raises ``TimeoutError``. No handler claimed that, so it
fell to the catch-all, which answers 500「服务器内部错误」 — a fault in the
owner process, which is the one thing a silent device is not. The caller could
only report it under its own name: `cleanup device inventory failed
device=a3dc2940aee2` carrying a bare `Server error '500'`, three times in 90
minutes on 2026-09-16.

The timeout is injected rather than waited out. What these pin is the
translation at the HTTP boundary; sitting through ``timeout + 5`` real seconds
to watch ``wait_for`` fire would test asyncio, and that ``exec`` raises on time
is device_hub's own behaviour, covered where it lives.
"""

from typing import Any

import httpx
import pytest

from app import device_connection_app
from app.core.config import settings
from app.domain.agent.device_hub import DeviceOffline, device_hub


@pytest.fixture(autouse=True)
def owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "device_connection_owner", True)
    monkeypatch.setattr(settings, "device_connection_secret", "test-owner-secret")
    monkeypatch.setattr(device_connection_app, "_release_draining", False)
    monkeypatch.setattr(device_connection_app, "_active_rpc_calls", 0)


def _owner_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=device_connection_app.app),
        base_url="http://owner",
        headers={"X-Device-Connection-Secret": "test-owner-secret"},
    )


async def _exec(**body: Any) -> httpx.Response:
    async with _owner_client() as client:
        return await client.post(
            "/internal/device-connection/call/exec",
            json={"device_id": "machine-7", "argv": ["true"], **body},
        )


@pytest.mark.anyio
async def test_a_silent_device_is_reported_as_a_timeout_not_an_owner_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_answers(*_args: Any, **_kwargs: Any) -> dict:
        raise TimeoutError

    monkeypatch.setattr(device_hub, "exec", never_answers)

    response = await _exec()

    assert response.status_code == 504
    assert response.json()["error"]["message"] == (
        "device machine-7 did not answer exec in time"
    )


@pytest.mark.anyio
async def test_a_timeout_does_not_claim_the_device_went_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``X-Device-Id`` is how the client tells an offline device from anything
    else. A connected-but-silent device must not carry it, or every timeout
    would arrive as a ``DeviceOffline``."""

    async def never_answers(*_args: Any, **_kwargs: Any) -> dict:
        raise TimeoutError

    monkeypatch.setattr(device_hub, "exec", never_answers)

    response = await _exec()

    assert "X-Device-Id" not in response.headers


@pytest.mark.anyio
async def test_a_timeout_names_the_call_even_without_a_device_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not every RPC carries a device id; the one that timed out is still named."""

    async def never_answers(*_args: Any, **_kwargs: Any) -> dict:
        raise TimeoutError

    monkeypatch.setattr(device_hub, "list_screens", never_answers)

    async with _owner_client() as client:
        response = await client.post(
            "/internal/device-connection/call/list_screens", json={}
        )

    assert response.status_code == 504
    assert (
        response.json()["error"]["message"]
        == "device did not answer list_screens in time"
    )


@pytest.mark.anyio
async def test_an_offline_device_still_answers_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-existing branch, pinned because the timeout branch sits beside
    it: the two are told apart by status, and offline is still 409.

    Whether the 409 also arrives carrying ``X-Device-Id`` is the exception
    handler's job, not this route's — it raises the header either way. That
    half is pinned in `test_error_response_headers.py`."""

    async def is_offline(*_args: Any, **_kwargs: Any) -> dict:
        raise DeviceOffline("machine-7")

    monkeypatch.setattr(device_hub, "exec", is_offline)

    response = await _exec()

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "device offline"


@pytest.mark.anyio
async def test_a_timeout_releases_the_call_it_was_counting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counter gates release-drain: a timeout that leaked one would leave
    the owner permanently 「busy」 and block its own deploy."""

    async def never_answers(*_args: Any, **_kwargs: Any) -> dict:
        raise TimeoutError

    monkeypatch.setattr(device_hub, "exec", never_answers)

    await _exec()

    assert device_connection_app._active_rpc_calls == 0
