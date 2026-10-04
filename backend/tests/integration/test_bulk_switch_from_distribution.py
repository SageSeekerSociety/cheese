"""A project manager lists the agent sessions on one self-hosted device and
switches some of them elsewhere (#1900 step 6): each switch is the room's own
(push first, per-session refusal), and a session whose room is mid-turn is
left alone with the reason 「正在运行任务，稍后再换」.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.common.auth import create_access_token
from app.domain.agent import execution
from app.domain.agent.models import AgentTurn
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.machine import session_work as work_lease
from app.domain.project.models import Project
from app.domain.team.models import TeamMemberRole, TeamUserRelation
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.executor_release import running
from tests.integration.conftest import post_project, registered, session_auth_headers

pytestmark = pytest.mark.anyio


def _signed_in(user_id, handle):
    return {"Authorization": "Bearer " + create_access_token(user_id, handle=handle)}


async def _project_on_a_device(client):
    """Alice's project with three rooms whose agents work on her device "old":
    one idle, one mid-turn, and a private room of Bob's she cannot open."""
    project = post_project(client, json={"name": "Orchard"}, owner="alice").json()[
        "data"
    ]
    project_id = uuid.UUID(project["id"])
    async with client.test_factory() as db:
        bob = await registered(db, "bob")
        now = datetime.now(UTC)
        db.add(
            TeamUserRelation(
                team_id=(await db.get(Project, project_id)).team_id,
                user_id=bob,
                role=TeamMemberRole.MEMBER,
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    rooms = {}
    for title, by in (("Idle", "alice"), ("Busy", "alice"), ("Bob only", "bob")):
        rooms[title] = uuid.UUID(
            client.post(
                "/topics",
                json={"project_id": project["id"], "title": title},
                headers=session_auth_headers(by),
            ).json()["data"]["id"]
        )
    async with client.test_factory() as db:
        alice = await db.scalar(select(User).where(User.username == "alice"))
        (await db.get(Topic, rooms["Bob only"])).is_private = True
        devices = sql_device_service(db)
        ids = []
        for name in ("old", "new"):
            device = await devices.approve(
                await devices.start(name),
                owner_user_id=alice.id,
                supply=Supply.self_hosted,
            )
            await devices.assign_to_project(
                device.device_id, project_id, actor_user_id=alice.id
            )
            ids.append(device.device_id)
        old, new = ids
        sessions = {}
        for title, topic_id in rooms.items():
            session = await AgentSessionService(db).ensure(
                topic_id, f"agent-{title[:3].lower()}", harness="claude-code"
            )
            generation = str(uuid.uuid4())
            session.execution_request = {
                "generation": generation,
                "choice": {"name": "Old", "profile": "device", "device_id": old},
                "authorized_by": None,
            }
            session.work_lease = {
                "kind": "device",
                "session_id": str(session.id),
                "generation": generation,
                "resource_id": str(uuid.uuid4()),
                "room_resource_id": str(topic_id),
                "device_id": old,
                "status": "ready",
                "home": "/old",
                "state": "/old/state",
                "workspace": "/old/work",
                "mcp_servers": [],
            }
            sessions[title] = session.id
        db.add(
            AgentTurn(
                id=uuid.uuid4(),
                topic_id=rooms["Busy"],
                continuation_id=uuid.uuid4(),
                author="alice",
                started_at=now,
            )
        )
        await db.commit()
    return SimpleNamespace(
        project_id=project_id,
        rooms=rooms,
        sessions=sessions,
        old=old,
        new=new,
        alice=_signed_in(alice.id, "alice"),
        bob=_signed_in(bob, "bob"),
    )


def _pushes(monkeypatch):
    monkeypatch.setattr(
        work_lease, "device_hub", SimpleNamespace(is_online=lambda device: True)
    )
    remote = AsyncMock(
        side_effect=lambda lease, method, *a, **k: (
            running() if method == "ping" else {"value": {"stdout": ""}}
        )
    )
    monkeypatch.setattr(execution, "call", remote)
    return remote


async def test_a_manager_lists_the_sessions_on_a_device(client):
    p = await _project_on_a_device(client)

    listed = client.get(
        f"/projects/{p.project_id}/devices/{p.old}/sessions", headers=p.alice
    )

    assert listed.status_code == 200, listed.text
    data = listed.json()["data"]
    rows = {row["topic_title"]: row for row in data["sessions"]}
    assert set(rows) == {"Idle", "Busy"}
    assert rows["Busy"]["working"] is True and rows["Idle"]["working"] is False
    assert rows["Idle"]["id"] == str(p.sessions["Idle"])
    assert rows["Idle"]["agent_name"] and rows["Idle"]["last_active"]
    # The private room is counted, not named.
    assert data["hidden"] == 1
    # Nothing is on the other device.
    empty = client.get(
        f"/projects/{p.project_id}/devices/{p.new}/sessions", headers=p.alice
    )
    assert empty.json()["data"] == {"sessions": [], "hidden": 0}


async def test_only_a_project_manager_lists_them(client):
    p = await _project_on_a_device(client)

    refused = client.get(
        f"/projects/{p.project_id}/devices/{p.old}/sessions", headers=p.bob
    )

    assert refused.status_code == 403, refused.text


async def test_a_bulk_switch_moves_an_idle_session_and_skips_one_mid_turn(
    client, monkeypatch
):
    p = await _project_on_a_device(client)
    remote = _pushes(monkeypatch)
    to_new = {
        "choice": {"name": "New", "profile": "device", "device_id": p.new},
        "if_idle": True,
    }

    def switch(title):
        return client.put(
            f"/topics/{p.rooms[title]}/compute-profile",
            headers=p.alice,
            json=to_new,
        )

    idle, busy = switch("Idle"), switch("Busy")

    assert idle.status_code == 200, idle.text
    assert busy.status_code == 409, busy.text
    assert busy.json()["error"]["name"] == "SessionWorking"
    assert busy.json()["error"]["message"] == "正在运行任务，稍后再换"
    # Only the idle session's machine was asked to push.
    pushes = [c for c in remote.await_args_list if c.args[1] == "control"]
    assert len(pushes) == 1
    async with client.test_factory() as db:
        moved = await AgentSessionService(db).by_id(p.sessions["Idle"])
        kept = await AgentSessionService(db).by_id(p.sessions["Busy"])
    assert moved.execution_request["choice"]["device_id"] == p.new
    assert kept.execution_request["choice"]["device_id"] == p.old
    assert kept.work_lease["device_id"] == p.old
    listed = client.get(
        f"/projects/{p.project_id}/devices/{p.old}/sessions", headers=p.alice
    ).json()["data"]["sessions"]
    assert [row["topic_title"] for row in listed] == ["Busy"]


async def test_without_if_idle_the_roster_switch_is_unchanged(client, monkeypatch):
    p = await _project_on_a_device(client)
    _pushes(monkeypatch)

    switched = client.put(
        f"/topics/{p.rooms['Busy']}/compute-profile",
        headers=p.alice,
        json={"choice": {"name": "New", "profile": "device", "device_id": p.new}},
    )

    assert switched.status_code == 200, switched.text
