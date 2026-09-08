"""Project machines end to end against a real database.

Covers what the unit tests can't: that the table the migration builds actually
holds a row, and that a deployment with no MicroCloud credentials says so
rather than failing somewhere inside a provider call.
"""

import asyncio
import uuid

from anyio.from_thread import BlockingPortal
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.actor import Actor
from app.domain.machine import enrollment
from app.domain.machine.models import MachineStatus
from app.domain.machine.repositories import ProjectMachineRepository
from app.domain.machine.services import MachineService
from app.domain.membership.repositories import MemberRepository
from app.domain.project.models import ProjectRole
from app.domain.project.repositories import ProjectRepository
from app.domain.project.services import ProjectService
from app.domain.team.models import TeamMemberRole
from app.domain.team.repositories import TeamRepository
from app.domain.topic.repositories import TopicRepository
from tests.conftest import seed_user
from tests.integration.conftest import UserCreator
from tests.unit.test_machine_service import FakeMicroCloud


def _project(client: TestClient, headers: dict[str, str] | None = None) -> str:
    return client.post(
        "/projects", json={"name": "机器项目"}, headers=headers or {}
    ).json()["data"]["id"]


async def _add_topic_machine(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    machine_id: int,
    status: MachineStatus = MachineStatus.deleted,
):
    return await ProjectMachineRepository(session).add(
        project_id=project_id,
        topic_id=topic_id,
        machine_id=machine_id,
        customer_id=7,
        account_id=9,
        offering_id=1,
        hostname=f"topic-cloud-{machine_id}",
        login_user="cheese",
        cores=2,
        memory_mb=4096,
        disk_gb=20,
        status=status,
        ip=None,
        requested_by="owner",
    )


def test_reads_report_an_unconfigured_deployment(api_client, auth_headers):
    # The test settings carry no MicroCloud credentials, so the feature must
    # name that plainly to an authorized member instead of surfacing a provider
    # stack trace or leaking deployment state to an anonymous caller.
    pid = _project(api_client, auth_headers)
    response = api_client.get(f"/projects/{pid}/machines", headers=auth_headers)
    assert response.status_code == 422
    assert "not configured" in response.json()["message"]


def test_provisioning_requires_a_real_credential(api_client):
    # Provisioning spends money and leaves a machine running, so it must be
    # refused before anything else is considered — including configuration.
    pid = _project(api_client)
    assert api_client.post(f"/projects/{pid}/machines", json={}).status_code == 401


def test_machine_inventory_requires_project_membership(
    api_client: TestClient,
    user_client: UserCreator,
    db_session: AsyncSession,
    _portal: BlockingPortal,
):
    owner = user_client.create_user()
    owner.token = user_client.login(api_client, owner.username, owner.password)
    member = user_client.create_user()
    member.token = user_client.login(api_client, member.username, member.password)
    outsider = user_client.create_user()
    outsider.token = user_client.login(api_client, outsider.username, outsider.password)
    member_headers = {"Authorization": f"Bearer {member.token}"}
    outsider_headers = {"Authorization": f"Bearer {outsider.token}"}

    async def _legacy_project_with_member() -> str:
        # ProjectService intentionally places every current user's new project
        # in their personal team. Seed a historical team-less row directly so
        # this test exercises the compatibility authorization branch.
        project = await ProjectRepository(db_session).add(
            name="Legacy machine project",
            owner_handle=owner.username,
            team_id=None,
        )
        await MemberRepository(db_session).add(
            project_id=project.id,
            user_handle=member.username,
            role=ProjectRole.member,
        )
        return str(project.id)

    pid = _portal.call(_legacy_project_with_member)

    # An authorized member reaches the deployment capability check. An outsider
    # sees neither that capability nor the project's private machine inventory.
    assert (
        api_client.get(f"/projects/{pid}/machines", headers=member_headers).status_code
        == 422
    )
    assert (
        api_client.get(
            f"/projects/{pid}/machines", headers=outsider_headers
        ).status_code
        == 404
    )
    assert (
        api_client.post(
            f"/projects/{pid}/machines", json={}, headers=outsider_headers
        ).status_code
        == 404
    )


def test_team_compute_is_visible_to_members_but_only_admins_can_spend(
    api_client: TestClient,
    user_client: UserCreator,
    db_session: AsyncSession,
    _portal: BlockingPortal,
):
    owner = user_client.create_user()
    owner.token = user_client.login(api_client, owner.username, owner.password)
    admin = user_client.create_user()
    admin.token = user_client.login(api_client, admin.username, admin.password)
    member = user_client.create_user()
    member.token = user_client.login(api_client, member.username, member.password)
    outsider = user_client.create_user()
    outsider.token = user_client.login(api_client, outsider.username, outsider.password)
    owner_headers = {"Authorization": f"Bearer {owner.token}"}

    team_response = api_client.post(
        "/teams",
        json={
            "name": f"Cloud team {uuid.uuid4().hex[:8]}",
            "intro": "",
            "description": "",
            "avatarId": 1,
        },
        headers=owner_headers,
    )
    team_id = int(team_response.json()["data"]["team"]["id"])

    async def _add_roles() -> None:
        repo = TeamRepository(db_session)
        await repo.add_member(team_id, admin.user_id, TeamMemberRole.ADMIN)
        await repo.add_member(team_id, member.user_id, TeamMemberRole.MEMBER)

    _portal.call(_add_roles)
    pid = api_client.post(
        "/projects",
        json={"name": "Team cloud", "team_id": team_id},
        headers=owner_headers,
    ).json()["data"]["id"]

    def headers(user) -> dict[str, str]:
        return {"Authorization": f"Bearer {user.token}"}

    # Members can inspect the shared pool. With no MicroCloud credentials in
    # tests, reaching the capability check is the observable 422.
    assert (
        api_client.get(f"/projects/{pid}/machines", headers=headers(member)).status_code
        == 422
    )
    # Provisioning/destroying paid infrastructure is team-admin only.
    assert (
        api_client.post(
            f"/projects/{pid}/machines", json={}, headers=headers(member)
        ).status_code
        == 403
    )
    assert (
        api_client.post(
            f"/projects/{pid}/machines", json={}, headers=headers(admin)
        ).status_code
        == 422
    )
    # Outsiders learn neither the project nor its private machine inventory.
    assert (
        api_client.get(
            f"/projects/{pid}/machines", headers=headers(outsider)
        ).status_code
        == 404
    )


def test_row_round_trips_through_the_migrated_table(
    db_session: AsyncSession, _portal: BlockingPortal
):
    async def _run() -> None:
        project = await ProjectRepository(db_session).add(name="机器项目")
        repo = ProjectMachineRepository(db_session)

        machine = await repo.add(
            project_id=project.id,
            machine_id=101,
            customer_id=7,
            account_id=9,
            offering_id=1,
            hostname="jiqi-abc123-1",
            login_user="cheese",
            cores=2,
            memory_mb=4096,
            disk_gb=20,
            status=MachineStatus.provisioning,
            ip=None,
            requested_by="andy",
        )

        listed = await repo.list_for_project(project.id)
        assert [m.id for m in listed] == [machine.id]
        assert machine.topic_id is None
        assert machine.released_at is None

        await repo.set_state(machine, status=MachineStatus.running, ip="10.0.1.10")
        reread = await repo.get(machine.id)
        assert reread is not None
        assert reread.status == MachineStatus.running
        assert reread.ip == "10.0.1.10"

        # A later read that omits the IP must not erase the way back in.
        await repo.set_state(machine, status=MachineStatus.stopping, ip=None)
        again = await repo.get(machine.id)
        assert again is not None and again.ip == "10.0.1.10"

        await repo.delete(machine)
        assert await repo.list_for_project(project.id) == []

    _portal.call(_run)


def test_machines_are_scoped_to_their_project(
    db_session: AsyncSession, _portal: BlockingPortal
):
    async def _run() -> None:
        projects = ProjectRepository(db_session)
        one = await projects.add(name="A")
        two = await projects.add(name="B")
        repo = ProjectMachineRepository(db_session)

        await repo.add(
            project_id=one.id,
            machine_id=1,
            customer_id=1,
            account_id=1,
            offering_id=1,
            hostname="a-1",
            login_user="cheese",
            cores=1,
            memory_mb=512,
            disk_gb=10,
            status=MachineStatus.running,
            ip="10.0.0.1",
            requested_by=None,
        )

        assert await repo.list_for_project(two.id) == []
        assert await repo.find_by_hostname(two.id, "a-1") is None
        assert await repo.find_by_hostname(one.id, "a-1") is not None
        assert await repo.get(uuid.uuid4()) is None

    _portal.call(_run)


def test_topic_cloud_provisioning_is_concurrent_safe_and_exclusive(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    token = seed_user(client, "owner")
    project_id = _project(client, {"Authorization": f"Bearer {token}"})
    first_topic_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "First", "created_by": "owner"},
    ).json()["data"]["id"]
    second_topic_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "Second", "created_by": "owner"},
    ).json()["data"]["id"]
    cloud = FakeMicroCloud()

    async def _keypair():
        return "private", "ssh-ed25519 public"

    monkeypatch.setattr(enrollment, "generate_keypair", _keypair)

    async def _authorized(_self, _project_id, _actor):
        return None

    monkeypatch.setattr(MachineService, "require_use_authority", _authorized)
    actor = Actor("owner", 1, False, "token")

    async def _ensure(topic_id: str) -> tuple[int, uuid.UUID]:
        async with client.test_factory() as session:
            machine = await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(topic_id), actor=actor
            )
            await session.commit()
            return machine.machine_id, machine.id

    async def _run():
        same_topic = await asyncio.gather(
            _ensure(first_topic_id), _ensure(first_topic_id)
        )
        other_topic = await _ensure(second_topic_id)
        return same_topic, other_topic

    same_topic, other_topic = asyncio.run(_run())

    assert same_topic[0] == same_topic[1]
    assert len(cloud.created) == 2
    assert other_topic[0] != same_topic[0][0]


def test_direct_and_cascading_archive_release_every_topic_machine(client):
    async def _seed():
        async with client.test_factory() as session:
            project = await ProjectRepository(session).add(name="Archive Cloud")
            topics = TopicRepository(session)
            direct = await topics.add(project_id=project.id, title="Direct")
            parent = await topics.add(project_id=project.id, title="Parent")
            child = await topics.add(
                project_id=project.id, parent_id=parent.id, title="Child"
            )
            for number, topic in enumerate((direct, parent, child), start=201):
                await _add_topic_machine(
                    session,
                    project_id=project.id,
                    topic_id=topic.id,
                    machine_id=number,
                )
            await session.commit()
            return direct.id, parent.id, child.id

    direct_id, parent_id, child_id = asyncio.run(_seed())
    assert client.post(f"/topics/{direct_id}/archive", json={"by": "u"}).is_success
    assert client.post(f"/topics/{parent_id}/archive", json={"by": "u"}).is_success

    async def _released():
        async with client.test_factory() as session:
            repo = ProjectMachineRepository(session)
            return [
                await repo.get_active_for_topic(tid)
                for tid in (direct_id, parent_id, child_id)
            ]

    assert asyncio.run(_released()) == [None, None, None]


def test_unarchived_topic_provisions_a_new_machine(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    seed_user(client, "owner")

    async def _seed():
        async with client.test_factory() as session:
            project = await ProjectService(session).create(
                name="Reactivate Cloud", owner_handle="owner"
            )
            topic = await TopicRepository(session).add(
                project_id=project.id, title="Again", created_by="owner"
            )
            old = await _add_topic_machine(
                session,
                project_id=project.id,
                topic_id=topic.id,
                machine_id=301,
            )
            await session.commit()
            return topic.id, old.id

    topic_id, old_id = asyncio.run(_seed())
    assert client.post(f"/topics/{topic_id}/archive", json={"by": "u"}).is_success
    assert client.post(f"/topics/{topic_id}/unarchive", json={"by": "u"}).is_success
    cloud = FakeMicroCloud()

    async def _keypair():
        return "private", "ssh-ed25519 public"

    monkeypatch.setattr(enrollment, "generate_keypair", _keypair)

    async def _authorized(_self, _project_id, _actor):
        return None

    monkeypatch.setattr(MachineService, "require_use_authority", _authorized)

    async def _reprovision():
        async with client.test_factory() as session:
            machine = await MachineService(session, cloud).ensure_topic_machine(
                topic_id, actor=Actor("owner", 1, False, "token")
            )
            old = await ProjectMachineRepository(session).get(old_id)
            await session.commit()
            return machine.id, old.released_at

    new_id, released_at = asyncio.run(_reprovision())
    assert new_id != old_id
    assert released_at is not None
    assert len(cloud.created) == 1
