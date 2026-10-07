"""A room is never held by a work computer it cannot use.

Two ways a room used to be stuck for good: its machine's install stopped
half-way (the machine went offline, or the request was abandoned) and every
later switch was refused as "still being allocated"; or its owner unbound the
machine — a re-bind enrols a new device id — and the room went on asking for
the old one, reporting it offline while the owner's device page showed the
machine online.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.agent import execution
from app.domain.topic.models import Topic
from tests.integration.test_switch_pushes_first import (
    PUSHED,
    _machines,
    _room,
    _session,
    _to_new,
)

pytestmark = pytest.mark.anyio


async def _lease_is(client, room, **changes):
    from app.domain.agent_session.services import AgentSessionService

    async with client.test_factory() as db:
        session = await AgentSessionService(db).by_id(room.session_id)
        session.work_lease = {**session.work_lease, **changes}
        for key in [key for key, value in changes.items() if value is None]:
            session.work_lease.pop(key)
        await db.commit()
        return dict(session.work_lease)


def _abandoned_install():
    """What a lease looks like after its install stopped: reserved, never
    installed, and nobody holding the claim any more."""
    return {
        "status": "preparing",
        "state": None,
        "workspace": None,
        "home": None,
        "mcp_servers": None,
        "claim": str(uuid.uuid4()),
        "claim_until": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    }


async def test_an_install_that_stopped_does_not_hold_the_room(client, monkeypatch):
    room = await _room(client)
    lease = await _lease_is(client, room, **_abandoned_install())
    remote = _machines(monkeypatch, online=False)

    # Its agent switches by itself: nothing is refused over work that could
    # not be pushed, and an install that stopped is no allocation in progress.
    agent = client.put(
        room.path,
        headers=room.agent,
        json={"profile": "device", "device_id": room.new_device},
    )

    assert agent.status_code == 200, agent.text
    session = await _session(client, room)
    assert session.execution_request["choice"]["device_id"] == room.new_device
    assert session.work_lease is None
    assert session.execution_request["retained_leases"] == [lease]
    remote.assert_not_awaited()


async def test_a_machine_waiting_on_its_environment_pushes_and_switches(
    client, monkeypatch
):
    room = await _room(client)
    await _lease_is(
        client, room, status="preparing", environment_status={"state": "failed"}
    )
    remote = _machines(monkeypatch)

    switched = client.put(room.path, headers=room.agent, json=_to_new(room))

    assert switched.status_code == 200, switched.text
    assert remote.await_args.args[2]["subtype"] == "checkpoint"
    session = await _session(client, room)
    assert session.execution_request["choice"]["device_id"] == room.new_device


async def test_an_install_in_progress_still_holds_the_switch(client, monkeypatch):
    room = await _room(client)
    await _lease_is(
        client,
        room,
        **{
            **_abandoned_install(),
            "claim_until": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        },
    )
    _machines(monkeypatch)

    refused = client.put(room.path, headers=room.person, json=_to_new(room))

    assert refused.status_code == 409, refused.text
    assert "分配仍在进行" in refused.json()["error"]["message"]


@pytest.mark.parametrize("ready", [True, False])
async def test_anyone_may_leave_a_machine_its_owner_unbound(client, monkeypatch, ready):
    room = await _room(client)
    if not ready:
        await _lease_is(client, room, **_abandoned_install())
    unbound = client.delete(
        f"/connector/my/devices/{room.old_device}", headers=room.person
    )
    assert unbound.status_code == 200, unbound.text
    remote = _machines(monkeypatch, push=PUSHED)

    switched = client.put(
        room.path,
        headers=room.agent,
        json={"profile": "device", "device_id": room.new_device},
    )

    assert switched.status_code == 200, switched.text
    remote.assert_not_awaited()
    session = await _session(client, room)
    assert session.execution_request["choice"]["device_id"] == room.new_device
    # Nothing can reach that machine again, so nothing waits on it for cleanup.
    assert session.execution_request["retained_leases"] == []


def _installing_hub(monkeypatch):
    async def install(device, argv, **kwargs):
        return {
            "exit": 0,
            "stdout": json.dumps(
                {
                    "state": f"/{device}/state",
                    "workspace": f"/{device}/work",
                    "mcp_servers": [],
                }
            ),
        }

    hub = SimpleNamespace(
        target=lambda _device: "linux-amd64",
        is_online=lambda device: True,
        exec=AsyncMock(side_effect=install),
    )
    from app.domain.machine import session_work as work_lease

    monkeypatch.setattr(work_lease, "device_hub", hub)
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={"capabilities": []}))
    return hub


async def test_a_room_whose_named_machine_was_unbound_says_so(client, monkeypatch):
    room = await _room(client)
    assert (
        client.delete(
            f"/connector/my/devices/{room.old_device}", headers=room.person
        ).status_code
        == 200
    )
    hub = _installing_hub(monkeypatch)

    answer = client.post(room.lease_path, headers=room.agent, json={"env": {}})

    assert answer.status_code == 200, answer.text
    said = answer.json()["data"]["unavailable"]
    assert "解绑" in said and "重新选择" in said
    hub.exec.assert_not_awaited()


async def test_an_automatic_room_leaves_an_unbound_machine_for_an_online_one(
    client, monkeypatch
):
    room = await _room(client)
    async with client.test_factory() as db:
        topic = await db.get(Topic, room.topic_id)
        topic.compute_config = {"name": "自有设备 · 自动选择", "profile": "device"}
        await db.commit()
    assert (
        client.delete(
            f"/connector/my/devices/{room.old_device}", headers=room.person
        ).status_code
        == 200
    )
    hub = _installing_hub(monkeypatch)

    answer = client.post(room.lease_path, headers=room.agent, json={"env": {}})

    assert answer.status_code == 200, answer.text
    assert answer.json()["data"]["target"]["device_id"] == room.new_device
    assert [call.args[0] for call in hub.exec.await_args_list] == [room.new_device]
