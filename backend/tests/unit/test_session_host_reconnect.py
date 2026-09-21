"""A transient connector outage preserves the chosen host and has a deadline."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.domain.agent import device_provider
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.channel import ScreenSetupError


@pytest.mark.anyio
async def test_startup_waits_for_its_host_to_reconnect(monkeypatch):
    online = set()
    asked = []

    def is_online(host):
        asked.append(host)
        return host in online

    channel = DeviceChannel(hub=SimpleNamespace(is_online=is_online))
    monkeypatch.setattr(device_provider, "_SESSION_RECONNECT_POLL_S", 0.005)
    asyncio.get_running_loop().call_later(0.02, online.add, "chosen-host")
    await channel._wait_for_session_host(
        "chosen-host", SessionRef(uuid.uuid4(), uuid.uuid4(), "reviewer", "pi")
    )
    assert len(asked) > 1
    assert set(asked) == {"chosen-host"}


@pytest.mark.anyio
async def test_startup_stops_when_the_pinned_host_stays_offline(monkeypatch):
    monkeypatch.setattr(device_provider, "_SESSION_RECONNECT_GRACE_S", 0.02)
    channel = DeviceChannel(hub=SimpleNamespace(is_online=lambda host: False))
    with pytest.raises(ScreenSetupError, match="未连接"):
        await asyncio.wait_for(
            channel._wait_for_session_host(
                "chosen-host", SessionRef(uuid.uuid4(), uuid.uuid4(), "reviewer", "pi")
            ),
            timeout=0.5,
        )


@pytest.mark.anyio
@pytest.mark.parametrize("needs_place", [True, False])
async def test_reconnect_leaves_database_connections_available(
    monkeypatch, needs_place
):
    from unittest.mock import AsyncMock

    from app.domain.agent.central_provider import CentralChannel
    from app.domain.agent.harness.channel import Placement

    active = False
    online = False

    class Database:
        async def __aenter__(self):
            nonlocal active
            active = True
            return self

        async def __aexit__(self, *args):
            nonlocal active
            active = False

        async def commit(self):
            pass

    def is_online(host):
        assert not active, "A reconnect must release its database connection"
        return online

    def reconnect():
        nonlocal online
        online = True

    device = DeviceChannel(
        hub=SimpleNamespace(is_online=is_online), session_factory=Database
    )
    device.precheck = AsyncMock(return_value=Placement("executor", 7, "reviewer", True))
    channel = CentralChannel(device)
    channel._resolve_session_host = AsyncMock(return_value="chosen-host")
    channel._session_agent = AsyncMock(
        return_value=SimpleNamespace(id=7, username="reviewer")
    )
    monkeypatch.setattr(device_provider, "_SESSION_RECONNECT_POLL_S", 0.005)
    asyncio.get_running_loop().call_later(0.02, reconnect)
    result = await channel.precheck(
        SessionRef(uuid.uuid4(), uuid.uuid4(), "reviewer", "codex"),
        needs_place=needs_place,
    )
    assert result.agent_handle == "reviewer"
    assert result.machine == ("executor" if needs_place else "chosen-host")
