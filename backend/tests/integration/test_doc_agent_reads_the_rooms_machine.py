"""A document's 芝士 reads the room's work on the machine the room already
holds, and only reads it.

It never takes a machine for a question: a room whose machine is not in hand
(none chosen, a device offline) lends none. What it lends reads files and
nothing else: no file written, no command started.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.domain.agent import execution
from app.domain.agent.document import machine as reading
from app.domain.agent.document.machine import machine_to_read
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.integration.conftest import (
    post_project,
    room_agent_seat,
    session_auth_headers,
)

pytestmark = pytest.mark.anyio


async def _room(client, *, lease: bool) -> tuple[uuid.UUID, uuid.UUID, str]:
    """A room whose agent session holds a machine (``lease``) or none yet."""
    project = post_project(client, json={"name": "Reads"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, room_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    async with client.test_factory() as db:
        owner = await db.scalar(select(User).where(User.username == "alice"))
        if owner is None:
            owner = User(
                username="alice",
                email="alice@example.test",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            db.add(owner)
            await db.flush()
        devices = sql_device_service(db)
        device = await devices.approve(
            await devices.start("ada"),
            owner_user_id=owner.id,
            supply=Supply.self_hosted,
        )
        await devices.assign_to_project(
            device.device_id, project_id, actor_user_id=owner.id
        )
        topic = await db.get(Topic, room_id)
        assert topic is not None
        resource = str(topic.resource_id or room_id)
        session = await AgentSessionService(db).ensure(
            room_id, "cheese", harness="claude-code"
        )
        session.runtime_location = {
            "device_id": "center",
            "resource_id": resource,
            "channel": "device",
        }
        if lease:
            generation = str(uuid.uuid4())
            session.execution_request = {"generation": generation}
            session.work_lease = {
                "kind": "device",
                "status": "ready",
                "session_id": str(session.id),
                "generation": generation,
                "resource_id": resource,
                "room_resource_id": resource,
                "device_id": device.device_id,
                "workspace": "/work/room",
                "mcp_servers": [],
                "url": f"http://api/topics/{room_id}/execution/session-{resource}",
            }
        await db.commit()
    return project_id, room_id, resource


async def _lent(client, monkeypatch, project_id, room_id, *, online: bool):
    monkeypatch.setattr(
        reading, "device_hub", SimpleNamespace(is_online=lambda _device: online)
    )
    seat = room_agent_seat(client, room_id)
    async with client.test_factory() as db:
        return await machine_to_read(
            db, project_id=project_id, room_id=room_id, seat=seat, ttl_s=600
        )


async def test_a_room_lends_the_machine_its_agent_works_on(client, monkeypatch):
    project_id, room_id, _ = await _room(client, lease=True)
    lent = await _lent(client, monkeypatch, project_id, room_id, online=True)
    assert lent is not None
    assert lent["workspace"] == "/work/room"


@pytest.mark.parametrize(("lease", "online"), [(False, True), (True, False)])
async def test_a_room_without_its_machine_in_hand_lends_none(
    client, monkeypatch, lease, online
):
    project_id, room_id, _ = await _room(client, lease=lease)
    assert await _lent(client, monkeypatch, project_id, room_id, online=online) is None


@pytest.mark.parametrize(
    ("method", "params", "admitted"),
    [
        ("control", {"subtype": "files", "operation": "read", "path": "a"}, True),
        ("control", {"subtype": "files", "operation": "grep", "path": "."}, True),
        ("control", {"subtype": "git", "command": "diff", "base": "main"}, True),
        ("control", {"subtype": "files", "operation": "write", "path": "a"}, False),
        ("control", {"subtype": "files", "operation": "mkdir", "path": "d"}, False),
        (
            "control",
            {"subtype": "files", "operation": "open", "path": "a", "write": True},
            False,
        ),
        (
            "control",
            {"subtype": "shell", "operation": "start", "body": "rm -rf ."},
            False,
        ),
        ("invoke", {"id": "1", "tool": "Bash", "args": {"command": "ls"}}, False),
        ("mcp", {"server": "x", "method": "tools/list"}, False),
    ],
)
async def test_the_lent_machine_is_only_read(
    client, monkeypatch, method, params, admitted
):
    project_id, room_id, resource = await _room(client, lease=True)
    lent = await _lent(client, monkeypatch, project_id, room_id, online=True)
    assert lent is not None
    remote = AsyncMock(return_value={"done": True})
    monkeypatch.setattr(execution, "call", remote)
    response = client.post(
        f"/topics/{room_id}/execution/session-{resource}",
        headers={"X-Cheese-Token": lent["execution_token"]},
        json={"method": method, "params": params},
    )
    if admitted:
        assert response.status_code == 200, response.text
    else:
        assert response.status_code == 403, response.text
        remote.assert_not_awaited()
