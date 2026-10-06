"""A business backend knows which build each enrolled machine runs.

Whether a session on a machine can have an isolated environment depends on the
system its connector was built for, which the machine names in its `hello`. The
machine says that to the connection owner; a rolling business backend only sees
the owner's view of it, so that view has to carry it. A room's compute profile
and a session's machine checkout both ask.
"""

import httpx
import pytest

from app import device_connection_app
from app.core.config import settings
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_hub_rpc import RemoteDeviceHub
from app.domain.device.supply import sandbox_unavailable
from tests.support import wire

SECRET = "test-owner-secret"


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


@pytest.mark.anyio
async def test_backend_reads_the_build_each_machine_announced(backend) -> None:
    await _connect("windows-box")
    await device_hub.on_device_message(
        "windows-box", {"t": "hello", "v": 3, "target": "windows-amd64"}
    )
    await _connect("linux-box")
    await device_hub.on_device_message(
        "linux-box", {"t": "hello", "v": 3, "target": "linux-amd64"}
    )
    await backend.start()

    assert backend.target("windows-box") == "windows-amd64"
    assert backend.target("linux-box") == "linux-amd64"
    assert sandbox_unavailable(backend.target("windows-box")) is not None
    assert sandbox_unavailable(backend.target("linux-box")) is None


@pytest.mark.anyio
async def test_backend_reads_no_build_before_the_machine_says_hello(backend) -> None:
    await _connect("quiet-box")
    await backend.start()

    assert backend.target("quiet-box") == ""
    assert backend.target("never-seen") == ""
    assert sandbox_unavailable(backend.target("quiet-box")) is None
