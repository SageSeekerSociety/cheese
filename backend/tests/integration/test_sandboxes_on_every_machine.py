"""A sandbox has the same lifecycle on every machine (#2320).

The rules, as a person would state them before any of this was built:

- a sandbox on a person's computer is destroyed once it has been idle for its
  spec's idle time, as one on the platform's machines is, and its agent is
  told it is in a new sandbox on its next tool call;
- a sandbox still in use is kept;
- a sandbox its agent moved away from is not left behind on the person's
  computer: the same sweep destroys it once idle;
- a computer that is offline is not touched; its sandbox goes once it is back.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

from app.domain.agent_session.models import LOST_KEY
from app.domain.agent_session.services import AgentSessionService
from app.domain.machine import lifecycle
from app.domain.machine import session_work as work_lease
from app.domain.machine.lifecycle import SandboxLifecycle
from app.domain.machine.models import SandboxHome
from tests.integration.test_switch_pushes_first import _machines, _owner_id, _room

pytestmark = pytest.mark.anyio


async def _on_alices_computers(client, monkeypatch):
    """A room whose agent works in a sandbox on alice's computers, placed by
    one tool call."""
    room = await _room(client)
    spec = f"devices_of:{await _owner_id(client)}"
    async with client.test_factory() as db:
        await AgentSessionService(db).set_environment(
            room.conversation_id,
            room.agent_handle,
            {"kind": "sandbox", "spec": spec},
            chosen_by=None,
        )
        await db.commit()
    _machines(monkeypatch)
    # The executor installs in the new sandbox.
    work_lease.device_hub.exec = AsyncMock(
        return_value={
            "exit": 0,
            "stdout": json.dumps(
                {"state": "/state", "workspace": "/work", "mcp_servers": []}
            ),
        }
    )
    leased = client.post(room.lease_path, headers=room.agent, json={"env": {}})
    assert leased.status_code == 200, leased.text
    room.device = leased.json()["data"]["target"]["device_id"]
    room.spec = spec
    return room


async def _home(client, room):
    async with client.test_factory() as db:
        return await db.scalar(
            select(SandboxHome).where(SandboxHome.topic_id == room.topic_id)
        )


async def _last_used(client, room, ago: timedelta):
    async with client.test_factory() as db:
        await db.execute(
            update(SandboxHome)
            .where(SandboxHome.topic_id == room.topic_id)
            .values(active_at=datetime.now(UTC) - ago)
        )
        await db.commit()


async def _sweep(client, monkeypatch, *, online=True):
    destroyed = AsyncMock(return_value={"destroyed": True})
    monkeypatch.setattr(lifecycle, "run_on_host", destroyed)
    hub = SimpleNamespace(is_online=lambda device: online)
    async with client.test_factory() as db:
        await SandboxLifecycle(db, hub=hub).destroy_idle()
    return destroyed


async def test_a_sandbox_on_a_persons_computer_goes_once_idle(client, monkeypatch):
    room = await _on_alices_computers(client, monkeypatch)
    home = await _home(client, room)
    assert (home.device_id, home.spec) == (room.device, room.spec)

    await _last_used(client, room, timedelta(minutes=11))
    destroyed = await _sweep(client, monkeypatch)

    assert destroyed.await_args.args[1:3] == (room.device, "destroy")
    assert await _home(client, room) is None
    async with client.test_factory() as db:
        session = await AgentSessionService(db).by_id(room.session_id)
        assert session.execution_request[LOST_KEY] is True


async def test_a_sandbox_in_use_is_kept(client, monkeypatch):
    room = await _on_alices_computers(client, monkeypatch)

    await _last_used(client, room, timedelta(minutes=2))
    destroyed = await _sweep(client, monkeypatch)

    destroyed.assert_not_awaited()
    assert await _home(client, room) is not None


async def test_a_sandbox_left_behind_is_destroyed_by_the_same_sweep(
    client, monkeypatch
):
    room = await _on_alices_computers(client, monkeypatch)
    moved = client.put(
        room.path,
        headers=room.person,
        json={"environment": {"kind": "self_hosted", "device_id": room.new_device}},
    )
    assert moved.status_code == 200, moved.text
    left = await _home(client, room)
    assert left is not None and left.session_id is None

    await _last_used(client, room, timedelta(minutes=11))
    destroyed = await _sweep(client, monkeypatch)

    assert destroyed.await_args.args[1:3] == (room.device, "destroy")
    assert await _home(client, room) is None


async def test_an_offline_computer_keeps_its_sandbox_until_it_is_back(
    client, monkeypatch
):
    room = await _on_alices_computers(client, monkeypatch)
    await _last_used(client, room, timedelta(minutes=11))

    destroyed = await _sweep(client, monkeypatch, online=False)

    destroyed.assert_not_awaited()
    assert await _home(client, room) is not None
    await _sweep(client, monkeypatch)
    assert await _home(client, room) is None
