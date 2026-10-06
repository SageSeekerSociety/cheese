"""Authorizing a local directory reaches the machine from a rolling backend.

Enrolled machines hold their link to the connection owner, and the business
backend that records a grant reaches them only through the owner. The grant set
has to cross that boundary like every other call to a machine: until it does,
the owner of the directory is told the push failed, and the machine keeps
enforcing the set it had before — a revoked directory included.
"""

import asyncio
from typing import Any

import httpx
import pytest

from app import device_connection_app
from app.core.config import settings
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_hub_rpc import RemoteDeviceHub
from app.domain.local_fs.enforcement import push_grants
from app.domain.local_fs.memory_repository import InMemoryLocalFsRepository
from app.domain.local_fs.paths import Platform
from app.domain.local_fs.records import GrantMode, GrantScope
from app.domain.local_fs.service import LocalDirectoryService
from tests.support import wire
from tests.support.hang import HANG_S

SECRET = "test-owner-secret"
OWNER = 7


@pytest.fixture
async def backend(monkeypatch):
    monkeypatch.setattr(settings, "device_connection_owner", True)
    monkeypatch.setattr(settings, "device_connection_secret", SECRET)
    monkeypatch.setattr(device_hub, "_devices", {})
    monkeypatch.setattr(device_hub, "_screens", {})
    monkeypatch.setattr(device_hub, "_by_screen_token", {})
    monkeypatch.setattr(device_connection_app, "_executor_calls", {})
    monkeypatch.setattr(device_connection_app, "_release_draining", False)
    remote = RemoteDeviceHub(
        "http://owner",
        SECRET,
        transport=httpx.ASGITransport(app=device_connection_app.app),
    )
    yield remote
    await remote.close()


async def _connect(device_id: str) -> wire.RecordingDevice:
    connector = wire.RecordingDevice()
    await device_hub.attach_device(device_id, connector)
    await connector.sent.get()  # welcome
    return connector


async def _grants_frame(connector: wire.RecordingDevice) -> dict[str, Any]:
    while True:
        frame = await asyncio.wait_for(connector.sent.get(), HANG_S)
        if frame.get("t") == "localfs.grants":
            return frame


async def _answer(device_id: str, frame: dict[str, Any], **reply: Any) -> None:
    await device_hub.on_device_message(
        device_id, {"t": "localfs.grants.result", "id": frame["id"], **reply}
    )


async def _granted(device_id: str, path: str) -> LocalDirectoryService:
    service = LocalDirectoryService(InMemoryLocalFsRepository())
    await service.grant_directory(
        device_id=device_id,
        owner_user_id=OWNER,
        path=path,
        platform=Platform.LINUX,
        mode=GrantMode.READ,
        scope=GrantScope.USER,
    )
    return service


@pytest.mark.anyio
async def test_a_grant_reaches_the_machine_through_the_owner(backend) -> None:
    connector = await _connect("laptop")
    await backend.start()
    service = await _granted("laptop", "/home/alice/Paper")

    pushing = asyncio.create_task(push_grants(service, backend, "laptop"))
    frame = await _grants_frame(connector)
    await _answer("laptop", frame, value={"fingerprint": "fp-1", "applied": True})
    outcome = await asyncio.wait_for(pushing, HANG_S)

    assert [g["path"] for g in frame["value"]["grants"]] == ["/home/alice/Paper"]
    assert outcome.delivered is True
    assert outcome.fingerprint == "fp-1"


@pytest.mark.anyio
async def test_a_machine_that_refuses_the_set_is_reported(backend) -> None:
    connector = await _connect("laptop")
    await backend.start()
    service = await _granted("laptop", "/home/alice/Paper")

    pushing = asyncio.create_task(push_grants(service, backend, "laptop"))
    frame = await _grants_frame(connector)
    await _answer("laptop", frame, error="grant store is read-only")
    outcome = await asyncio.wait_for(pushing, HANG_S)

    assert outcome.delivered is False
    assert outcome.reason == "device_error"
    assert "grant store is read-only" in outcome.detail


@pytest.mark.anyio
async def test_an_owner_that_does_not_know_the_call_yet_is_reported() -> None:
    # The owner is released on its own and outlives ordinary deploys, so a
    # backend can be newer than the owner it calls, which answers a call it does
    # not have with 404. The grant is recorded either way; this is reported.
    def older_owner(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/snapshot"):
            return httpx.Response(200, json={"devices": [], "screens": []})
        return httpx.Response(404, json={"detail": "unknown device connection call"})

    remote = RemoteDeviceHub(
        "http://owner", SECRET, transport=httpx.MockTransport(older_owner)
    )
    try:
        service = await _granted("laptop", "/home/alice/Paper")
        outcome = await push_grants(service, remote, "laptop")
    finally:
        await remote.close()

    assert outcome.delivered is False
    assert outcome.reason == "platform_error"
    assert "http" not in outcome.detail


@pytest.mark.anyio
async def test_a_grant_for_a_machine_that_is_away_is_kept(backend) -> None:
    await backend.start()
    service = await _granted("closed-laptop", "/home/alice/Paper")

    outcome = await push_grants(service, backend, "closed-laptop")

    assert outcome.delivered is False
    assert outcome.reason == "device_offline"
