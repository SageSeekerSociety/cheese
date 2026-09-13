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
from app.domain.identity.actor import Actor
from app.domain.machine.services import MachineService
from app.domain.membership.repositories import MemberRepository
from app.domain.project.models import ProjectRole
from app.domain.project.repositories import ProjectRepository
from app.domain.team.models import TeamMemberRole
from app.domain.team.repositories import TeamRepository
from app.domain.topic.services import TopicService
from app.domain.user.repositories import UserRepository
from tests.conftest import seed_user
from tests.unit.test_machine_service import FakeMicroCloud


def setup_project(client, monkeypatch):
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    client.headers["Authorization"] = f"Bearer {seed_user(client, 'config_owner')}"
    response = client.post(
        "/projects", json={"name": "Compute", "owner_handle": "config_owner"}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def new_room(client, pid):
    response = client.post(
        "/topics",
        json={"project_id": pid, "title": "Room", "created_by": "config_owner"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def test_room_choice_does_not_change_project_default_or_new_room(client, monkeypatch):
    pid = setup_project(client, monkeypatch)
    tid = new_room(client, pid)
    original = client.get(f"/projects/{pid}/compute-configs").json()["data"]
    assert original["default"]["profile"] == "cloud"
    assert original["favorites"] == []
    choice = ComputeChoice(
        name="Large memory", profile="cloud", cores=8, memory_mb=16384, disk_gb=64
    ).model_dump()
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
                visibility=Visibility.host,
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
    response = client.put(
        f"/projects/{pid}/compute-configs", json={"default": choice, "favorites": []}
    )
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
            assert (
                await resolve_pinned_device(
                    devices, lambda _: True, project.id, topic.id
                )
                == device_id
            )
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
            json={"default": outside, "favorites": []},
        ).status_code
        == 422
    )


def test_team_member_uses_cloud_but_cannot_edit_project_defaults(client, monkeypatch):
    pid = setup_project(client, monkeypatch)
    tid = new_room(client, pid)
    member_token = seed_user(client, "config_member")

    async def join():
        async with client.test_factory() as session:
            project = await ProjectRepository(session).get(uuid.UUID(pid))
            member = await UserRepository(session).get_by_username("config_member")
            await TeamRepository(session).add_member(
                project.team_id, member.id, TeamMemberRole.MEMBER
            )
            await MemberRepository(session).add(
                project_id=project.id,
                user_handle=member.username,
                role=ProjectRole.member,
            )
            await session.commit()
            return member.id

    member_id = asyncio.run(join())
    client.headers["Authorization"] = f"Bearer {member_token}"
    choice = ComputeChoice(
        name="Cloud", profile="cloud", cores=2, memory_mb=4096, disk_gb=32
    ).model_dump()
    assert (
        client.put(
            f"/topics/{tid}/compute-profile", json={"choice": choice}
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/projects/{pid}/compute-configs",
            json={"default": choice, "favorites": []},
        ).status_code
        == 403
    )
    cloud = FakeMicroCloud()

    async def provision():
        async with client.test_factory() as session:
            machine = await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(tid), actor=Actor("config_member", member_id, False, "token")
            )
            await session.commit()
            return machine

    machine = asyncio.run(provision())
    assert (machine.cores, machine.memory_mb, machine.disk_gb) == (2, 4096, 32)
    assert (
        client.put(
            f"/topics/{tid}/compute-profile", json={"choice": {**choice, "cores": 4}}
        ).status_code
        == 422
    )
