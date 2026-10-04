"""A device's owner is told when an agent session starts working on it (#1900
step 5): which project, which room, which agent, and whether it can see the
whole machine. One notice per session start, not one per tool call; an owner
who is a person in that room is not told. The team page shows the owner who is
on each of their machines now, including one attached to a project and not
registered for the team.
"""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.common.auth import create_access_token
from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.machine import session_work as work_lease
from app.domain.notification.models import Notification
from app.domain.project.models import Project
from app.domain.team.models import TeamMemberRole, TeamUserRelation
from app.domain.topic.models import Topic
from tests.integration.conftest import post_project, registered, session_auth_headers

pytestmark = pytest.mark.anyio


async def _room_on_a_device(client, owner_handle, *, attached_to="team"):
    """Alice's room, whose agent session will work on ``owner_handle``'s device,
    which is registered for the project's team, or with ``attached_to="project"``
    attached to the project alone."""
    project = post_project(client, json={"name": "Orchard"}, owner="alice").json()[
        "data"
    ]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Pricing"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    async with client.test_factory() as db:
        owner_id = await registered(db, owner_handle)
        team_id = (await db.get(Project, project_id)).team_id
        if owner_handle != "alice":
            now = datetime.now(UTC)
            db.add(
                TeamUserRelation(
                    team_id=team_id,
                    user_id=owner_id,
                    role=TeamMemberRole.MEMBER,
                    created_at=now,
                    updated_at=now,
                )
            )
        devices = sql_device_service(db)
        device = await devices.approve(
            await devices.start("workstation"),
            owner_user_id=owner_id,
            supply=Supply.self_hosted,
        )
        if attached_to == "team":
            await devices.assign_to_team(
                device.device_id, team_id, actor_user_id=owner_id
            )
        else:
            await devices.assign_to_project(
                device.device_id, project_id, actor_user_id=owner_id
            )
        agent = await IdentityService(db).ensure_room_agent_user(topic_id)
        topic = await db.get(Topic, topic_id)
        resource = str(topic.resource_id or topic_id)
        session = await AgentSessionService(db).ensure(
            topic_id, agent.username, harness="claude-code"
        )
        session.execution_request = {
            "generation": str(uuid.uuid4()),
            "choice": {
                "name": "Workstation",
                "profile": "device",
                "device_id": device.device_id,
            },
        }
        session.runtime_location = {
            "device_id": "center",
            "resource_id": resource,
            "channel": "device",
        }
        token = bind_resource_token(
            mint_scoped_token(
                project_id=str(project_id),
                topic_id=str(topic_id),
                agent_handle=agent.username,
            ),
            resource,
            session_id=str(session.id),
        )
        await db.commit()
    return SimpleNamespace(
        team_id=team_id,
        owner_id=owner_id,
        owner=owner_handle,
        project_id=project_id,
        device_id=device.device_id,
        lease_path=f"/topics/{topic_id}/sessions/{session.id}/work-lease",
        agent={"X-Cheese-Token": token},
    )


def _machines(monkeypatch):
    async def install(device, argv, **kwargs):
        return {
            "exit": 0,
            "stdout": json.dumps(
                {"state": "/w/state", "workspace": "/w/work", "mcp_servers": []}
            ),
        }

    monkeypatch.setattr(
        work_lease,
        "device_hub",
        SimpleNamespace(is_online=lambda d: True, exec=AsyncMock(side_effect=install)),
    )
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={}))


async def _told(client, user_id):
    async with client.test_factory() as db:
        return (
            await db.scalars(
                select(Notification).where(
                    Notification.receiver_id == user_id,
                    Notification.type == "DEVICE_IN_USE",
                )
            )
        ).all()


def _signed_in(user_id, handle):
    return {"Authorization": "Bearer " + create_access_token(user_id, handle=handle)}


async def test_the_owner_is_told_once_when_an_agent_starts_on_their_device(
    client, monkeypatch
):
    room = await _room_on_a_device(client, "bob")
    _machines(monkeypatch)

    for _ in range(2):
        leased = client.post(room.lease_path, headers=room.agent, json={"env": {}})
        assert leased.status_code == 200, leased.text
        assert leased.json()["data"]["target"]["device_id"] == room.device_id

    told = await _told(client, room.owner_id)
    assert len(told) == 1
    said = told[0].metadata_payload
    assert said["projectName"] == "Orchard"
    assert said["topicTitle"] == "Pricing"
    assert said["agentName"]
    assert said["deviceName"] == "workstation"
    assert said["machineAccess"] is True
    assert "能访问整台机器" in said["content"]


async def test_an_owner_in_the_room_is_not_told(client, monkeypatch):
    room = await _room_on_a_device(client, "alice")
    _machines(monkeypatch)

    leased = client.post(room.lease_path, headers=room.agent, json={"env": {}})

    assert leased.status_code == 200, leased.text
    assert await _told(client, room.owner_id) == []


async def test_the_team_page_shows_the_owner_who_is_on_their_device(
    client, monkeypatch
):
    room = await _room_on_a_device(client, "bob")
    _machines(monkeypatch)
    path = f"/connector/teams/{room.team_id}/devices"
    before = client.get(path, headers=_signed_in(room.owner_id, "bob"))
    assert before.json()["devices"][0]["in_use"] == []

    client.post(room.lease_path, headers=room.agent, json={"env": {}})

    listed = client.get(path, headers=_signed_in(room.owner_id, "bob"))
    assert listed.status_code == 200, listed.text
    [device] = listed.json()["devices"]
    [user] = device["in_use"]
    assert (user["project_name"], user["topic_title"]) == ("Orchard", "Pricing")
    assert user["agent_name"]
    # Another member of the team sees the machine, not whose rooms are on it.
    async with client.test_factory() as db:
        alice = await registered(db, "alice")
    other = client.get(path, headers=_signed_in(alice, "alice")).json()["devices"]
    assert other[0]["in_use"] is None


async def test_the_team_page_lists_a_device_attached_only_to_a_project(
    client, monkeypatch
):
    room = await _room_on_a_device(client, "bob", attached_to="project")
    _machines(monkeypatch)
    leased = client.post(room.lease_path, headers=room.agent, json={"env": {}})
    assert leased.status_code == 200, leased.text
    path = f"/connector/teams/{room.team_id}/devices"

    listed = client.get(path, headers=_signed_in(room.owner_id, "bob"))

    assert listed.status_code == 200, listed.text
    [device] = listed.json()["devices"]
    assert device["device_id"] == room.device_id
    assert device["team_ids"] == []
    assert device["attached_projects"] == [
        {"id": str(room.project_id), "name": "Orchard"}
    ]
    [user] = device["in_use"]
    assert (user["project_name"], user["topic_title"]) == ("Orchard", "Pricing")
    async with client.test_factory() as db:
        alice = await registered(db, "alice")
    [seen] = client.get(path, headers=_signed_in(alice, "alice")).json()["devices"]
    assert seen["device_id"] == room.device_id
    assert seen["in_use"] is None


async def test_a_device_attached_to_another_teams_project_is_not_listed(client):
    room = await _room_on_a_device(client, "bob")
    elsewhere = post_project(client, json={"name": "Elsewhere"}, owner="bob").json()[
        "data"
    ]
    async with client.test_factory() as db:
        devices = sql_device_service(db)
        other = await devices.approve(
            await devices.start("laptop"),
            owner_user_id=room.owner_id,
            supply=Supply.self_hosted,
        )
        await devices.assign_to_project(
            other.device_id, uuid.UUID(elsewhere["id"]), actor_user_id=room.owner_id
        )
        await db.commit()

    listed = client.get(
        f"/connector/teams/{room.team_id}/devices",
        headers=_signed_in(room.owner_id, "bob"),
    ).json()["devices"]

    assert [d["device_id"] for d in listed] == [room.device_id]
