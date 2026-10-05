"""Every place that names the agent behind a session on a device names the
teammate the session belongs to: the bulk switch's session list, the team
page's 「正在用」 lines and the notice its device's owner gets. A session of a
project teammate carries that teammate's name; one that names no teammate
carries the name of the project's default agent.
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
from app.domain.agent_instance.services import AgentInstanceService
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

# The name each session should be shown under, by the session.
EXPECTED = {"kimi": "Kimi", "opus": "Opus", "default": "芝士", "legacy": "芝士"}


def _signed_in(user_id, handle):
    return {"Authorization": "Bearer " + create_access_token(user_id, handle=handle)}


async def _teammates_on_bobs_device(client):
    """Alice's project with two teammates besides its default agent. In one
    room each of them has a session, plus one under a handle that is no
    teammate's; all four will work on Bob's device, attached to the project."""
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
        alice = await registered(db, "alice")
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
        agents = AgentInstanceService(db)
        default = await agents.materialize_default(await db.get(Project, project_id))
        # A session is keyed by its teammate's handle; it acts under the seat
        # that teammate has on rosters.
        handles, seats = {"default": default.handle}, {}
        seats["default"] = await agents.ensure_identity(default)
        for key, handle, name in (
            ("kimi", "cheese-kimi", "Kimi"),
            ("opus", "cheesex-opus-cc", "Opus"),
        ):
            instance = await agents.create(
                project_id=project_id, handle=handle, type_name=None, display_name=name
            )
            handles[key] = instance.handle
            seats[key] = await agents.ensure_identity(instance)
        handles["legacy"] = seats["legacy"] = (
            await IdentityService(db).ensure_room_agent_user(topic_id)
        ).username
        devices = sql_device_service(db)
        device = await devices.approve(
            await devices.start("workstation"),
            owner_user_id=bob,
            supply=Supply.self_hosted,
        )
        await devices.assign_to_project(device.device_id, project_id, actor_user_id=bob)
        topic = await db.get(Topic, topic_id)
        resource = str(topic.resource_id or topic_id)
        leases = {}
        for key, handle in handles.items():
            session = await AgentSessionService(db).ensure(
                topic_id, handle, harness="claude-code"
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
                    agent_handle=seats[key],
                ),
                resource,
                session_id=str(session.id),
            )
            leases[key] = (
                f"/topics/{topic_id}/sessions/{session.id}/work-lease",
                {"X-Cheese-Token": token},
            )
        await db.commit()
    return SimpleNamespace(
        project_id=project_id,
        team_id=(await _team_of(client, project_id)),
        device_id=device.device_id,
        handles=handles,
        leases=leases,
        alice=_signed_in(alice, "alice"),
        bob_id=bob,
        bob=_signed_in(bob, "bob"),
    )


async def _team_of(client, project_id):
    async with client.test_factory() as db:
        return (await db.get(Project, project_id)).team_id


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
        SimpleNamespace(
            target=lambda _device: "linux-amd64",
            is_online=lambda d: True,
            exec=AsyncMock(side_effect=install),
        ),
    )
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={}))


async def _all_at_work(client, monkeypatch):
    p = await _teammates_on_bobs_device(client)
    _machines(monkeypatch)
    for path, headers in p.leases.values():
        leased = client.post(path, headers=headers, json={"env": {}})
        assert leased.status_code == 200, leased.text
        assert leased.json()["data"]["target"]["device_id"] == p.device_id
    return p


def _by_handle(p, rows, name_key="agent_name", handle_key="agent_handle"):
    keys = {handle: key for key, handle in p.handles.items()}
    return {keys[row[handle_key]]: row[name_key] for row in rows}


async def test_the_bulk_switch_names_each_sessions_teammate(client, monkeypatch):
    p = await _all_at_work(client, monkeypatch)

    listed = client.get(
        f"/projects/{p.project_id}/devices/{p.device_id}/sessions", headers=p.alice
    )

    assert listed.status_code == 200, listed.text
    assert _by_handle(p, listed.json()["data"]["sessions"]) == EXPECTED


async def test_the_team_page_names_each_sessions_teammate(client, monkeypatch):
    p = await _all_at_work(client, monkeypatch)

    listed = client.get(f"/connector/teams/{p.team_id}/devices", headers=p.bob)

    assert listed.status_code == 200, listed.text
    [device] = listed.json()["devices"]
    assert _by_handle(p, device["in_use"]) == EXPECTED


async def test_the_owner_is_told_which_teammate_started(client, monkeypatch):
    p = await _all_at_work(client, monkeypatch)

    async with client.test_factory() as db:
        told = (
            await db.scalars(
                select(Notification).where(
                    Notification.receiver_id == p.bob_id,
                    Notification.type == "DEVICE_IN_USE",
                )
            )
        ).all()

    said = [n.metadata_payload for n in told]
    assert _by_handle(p, said, "agentName", "agentHandle") == EXPECTED
    kimi = next(s for s in said if s["agentHandle"] == p.handles["kimi"])
    assert kimi["content"].startswith("Kimi 开始在「workstation」上工作")
