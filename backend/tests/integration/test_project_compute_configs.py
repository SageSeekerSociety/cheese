"""Project defaults, team-owned devices, and room-local choices."""

import asyncio
import uuid

import pytest

from app.core.config import settings
from app.domain.agent.compute_configs import ComputeChoice, bind_room_device_choice
from app.domain.agent.device_provider import resolve_pinned_device
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.device.supply import Supply, Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.project.repositories import ProjectRepository
from app.domain.team.models import TeamMemberRole
from app.domain.team.repositories import TeamRepository
from app.domain.topic.services import TopicService
from app.domain.user.repositories import UserRepository
from tests.conftest import seed_user
from tests.integration.conftest import post_project


def setup_project(client, monkeypatch, *, shared_team: bool = False):
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    client.headers["Authorization"] = f"Bearer {seed_user(client, 'config_owner')}"
    body = {"name": "Compute"}
    if shared_team:
        # Without it the project sits in the owner's personal team, which
        # nobody else can join.
        response = client.post(
            "/teams",
            json={
                "name": "Compute Team",
                "intro": "",
                "description": "",
                "avatarId": 1,
            },
        )
        assert response.status_code == 201, response.text
        body["team_id"] = response.json()["data"]["team"]["id"]
    response = post_project(client, json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def new_room(client, pid):
    response = client.post(
        "/topics",
        json={"project_id": pid, "title": "Room"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def test_room_choice_does_not_change_project_default_or_new_room(client, monkeypatch):
    pid = setup_project(client, monkeypatch)
    tid = new_room(client, pid)
    original = client.get(f"/projects/{pid}/compute-configs").json()["data"]
    assert original["default"]["profile"] == "cloud"
    assert original["distribution"] == {"cloud": 0, "cloud_vm": 0, "devices": []}
    choice = ComputeChoice(profile="cloud").model_dump()
    response = client.put(f"/topics/{tid}/compute-profile", json={"choice": choice})
    assert response.status_code == 200, response.text
    assert (
        client.get(f"/topics/{tid}/compute-profile").json()["data"]["choice"] == choice
    )
    assert (
        client.get(f"/projects/{pid}/compute-configs").json()["data"]["default"]
        == original["default"]
    )
    other = new_room(client, pid)
    assert (
        client.get(f"/topics/{other}/compute-profile").json()["data"]["choice"]
        == original["default"]
    )


def test_team_device_can_be_project_default_without_project_assignment(
    client, monkeypatch
):
    pid = setup_project(client, monkeypatch)

    async def register():
        async with client.test_factory() as session:
            project = await ProjectRepository(session).get(uuid.UUID(pid))
            owner = await UserRepository(session).get_by_username("config_owner")
            devices = sql_device_service(session)
            code = await devices.start("Lab workstation")
            device = await devices.approve(
                code,
                owner_user_id=owner.id,
                supply=Supply.self_hosted,
            )
            await devices.assign_to_team(
                device.device_id, project.team_id, actor_user_id=owner.id
            )
            await session.commit()
            return device.device_id

    device_id = asyncio.run(register())
    choice = ComputeChoice(
        name="Lab workstation", profile="device", device_id=device_id
    ).model_dump()
    response = client.put(f"/projects/{pid}/compute-configs", json={"default": choice})
    assert response.status_code == 200, response.text
    tid = new_room(client, pid)
    room = client.get(f"/topics/{tid}/compute-profile").json()["data"]
    assert room["device_id"] == device_id
    assert room["choice"] == choice
    assert room["devices"][0]["device_id"] == device_id

    async def execute_and_revoke():
        async with client.test_factory() as session:
            project = await ProjectRepository(session).get(uuid.UUID(pid))
            topic = await TopicService(session).get_or_404(uuid.UUID(tid))
            await bind_room_device_choice(session, topic, project.settings)
            devices = sql_device_service(session)
            assert (await devices.topic_binding(topic.id)).device_id == device_id
            owner = await UserRepository(session).get_by_username("config_owner")
            await devices.unassign_from_team(
                device_id, project.team_id, actor_user_id=owner.id
            )
            with pytest.raises(ScreenSetupError, match="已移出"):
                await resolve_pinned_device(
                    devices, lambda _: True, project.id, topic.id
                )
            await session.rollback()

    asyncio.run(execute_and_revoke())
    outside = {**choice, "device_id": "not-shared"}
    assert (
        client.put(
            f"/projects/{pid}/compute-configs",
            json={"default": outside},
        ).status_code
        == 422
    )


def test_team_member_uses_cloud_but_cannot_edit_project_defaults(client, monkeypatch):
    pid = setup_project(client, monkeypatch, shared_team=True)
    tid = new_room(client, pid)
    member_token = seed_user(client, "config_member")

    async def join():
        async with client.test_factory() as session:
            project = await ProjectRepository(session).get(uuid.UUID(pid))
            member = await UserRepository(session).get_by_username("config_member")
            await TeamRepository(session).add_member(
                project.team_id, member.id, TeamMemberRole.MEMBER
            )
            await session.commit()
            return member.id

    asyncio.run(join())
    client.headers["Authorization"] = f"Bearer {member_token}"
    choice = ComputeChoice(profile="cloud").model_dump()
    assert (
        client.put(
            f"/topics/{tid}/compute-profile", json={"choice": choice}
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/projects/{pid}/compute-configs",
            json={"default": choice},
        ).status_code
        == 403
    )


def test_the_project_shows_where_its_started_agents_work(client, monkeypatch):
    """「现在的分布」数的是已经开工的会话：云端几个，每台自有设备几个。

    还没开工的会话没有机器，由项目默认决定，不算进来；归档了的房间也不算。
    """
    from app.domain.agent_session.services import AgentSessionService
    from app.domain.topic.models import Topic, TopicStatus

    pid = setup_project(client, monkeypatch)
    room = new_room(client, pid)
    archived = new_room(client, pid)
    lab = ComputeChoice(name="Lab", profile="device", device_id=None)
    cloud = ComputeChoice(name="云端 · 标准配置", profile="cloud")

    async def seed():
        async with client.test_factory() as session:
            project = await ProjectRepository(session).get(uuid.UUID(pid))
            owner = await UserRepository(session).get_by_username("config_owner")
            devices = sql_device_service(session)
            code = await devices.start("Lab")
            device = await devices.approve(
                code,
                owner_user_id=owner.id,
                supply=Supply.self_hosted,
            )
            await devices.assign_to_team(
                device.device_id, project.team_id, actor_user_id=owner.id
            )
            on_lab = lab.model_copy(update={"device_id": device.device_id})
            sessions = AgentSessionService(session)

            async def agent(topic, handle, choice, lease=None):
                row = await sessions.ensure(
                    uuid.UUID(topic), handle, harness="claude-code"
                )
                if choice is not None:
                    row.execution_request = {
                        "generation": str(uuid.uuid4()),
                        "choice": choice.model_dump(),
                        "authorized_by": None,
                    }
                row.work_lease = lease

            await agent(room, "analyst", cloud)
            await agent(
                room,
                "builder",
                lab,
                {"kind": "device", "device_id": device.device_id, "status": "ready"},
            )
            await agent(room, "writer", on_lab)
            await agent(room, "idle", None)
            # Its owner gave this room the whole machine (#2320).
            await devices.bind_topic_device(
                uuid.UUID(room), device.device_id, Visibility.host
            )
            await agent(archived, "retired", on_lab)
            (
                await session.get(Topic, uuid.UUID(archived))
            ).status = TopicStatus.archived
            await session.commit()
            return device.device_id

    device_id = asyncio.run(seed())

    body = client.get(f"/projects/{pid}/compute-configs").json()["data"]

    assert body["distribution"] == {
        "cloud": 1,
        "cloud_vm": 0,
        "devices": [
            {
                "device_id": device_id,
                "name": "Lab",
                "agents": 2,
                "machine_access": True,
            }
        ],
    }
