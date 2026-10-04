"""Room admission and recovery over real database transactions."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text

from app.core.config import settings
from app.core.errors import AuthenticationRequiredError, ForbiddenError, ValidationError
from app.domain.agent.compute_configs import ComputeChoice
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.agent_session.models import AgentSession
from app.domain.block.repositories import BlockRepository
from app.domain.device.models import DeviceRow, DeviceTeamRow
from app.domain.device.supply import Supply
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.machine import owner_reads as machine_owner_reads
from app.domain.machine import progress
from app.domain.machine.microcloud import MicroCloudError
from app.domain.machine.models import (
    MAX_PROVIDER_ERRORS,
    AiStatus,
    MachineStatus,
    ProjectMachine,
    WarmMachine,
)
from app.domain.machine.repositories import ProjectMachineRepository
from app.domain.machine.services import CloudKeepsFailing, MachineService
from app.domain.machine.warm import WarmPoolService
from app.domain.topic.models import Topic
from app.domain.user.repositories import UserRepository
from tests.conftest import seed_user
from tests.integration.test_project_machines import _project
from tests.unit.test_machine_service import FakeMicroCloud


async def _sessions_for_cloud(client, topic_id):
    async with client.test_request_factory() as db:
        topic = await db.get(Topic, uuid.UUID(topic_id))
        result = []
        for handle in ("cloud-a", "cloud-b"):
            await AgentInstanceService(db).create(
                project_id=topic.project_id,
                handle=handle,
                type_name=None,
                display_name=handle,
            )
            row = AgentSession(
                topic_id=topic.id, agent_handle=handle, harness="claude-code"
            )
            db.add(row)
            await db.flush()
            result.append(row.id)
        await db.commit()
        return result


def test_sessions_in_one_room_share_the_rooms_cloud_machine(warm_case):
    client, topics, actor, cloud = warm_case
    choice = ComputeChoice(name="Cloud", profile="cloud")

    async def run():
        first, second = await _sessions_for_cloud(client, topics[0])

        async def ensure(session_id):
            async with client.test_request_factory() as db:
                machine = await MachineService(db, cloud).ensure_session_machine(
                    session_id, actor=actor, choice=choice
                )
                await db.commit()
                return machine.id, machine.machine_id, machine.owner_user_id

        a, duplicate, b = await asyncio.gather(
            ensure(first), ensure(first), ensure(second)
        )
        # 一个话题一个容器: one machine rented for the room, whoever asks first.
        assert a == duplicate == b
        assert len(cloud.created) + len(cloud.claims) == 1
        async with client.test_request_factory() as db:
            service = MachineService(db, cloud)
            assert await service.topic_machine(uuid.UUID(topics[0])) is None
            rows = await service.list_active_for_topic(uuid.UUID(topics[0]))
            assert [row.id for row in rows] == [a[0]]
            assert rows[0].session_id in {first, second}
            assert await service.ready_topic_devices() == []
            for row in rows:
                row.status = MachineStatus.error
            await db.flush()
            assert await service.failed_topic_leases() == []
            # Reopening detaches both allocations, without deleting either VM.
            await service.detach_archived_machine(uuid.UUID(topics[0]))
            await db.commit()
            assert not await service.list_active_for_topic(uuid.UUID(topics[0]))
            assert cloud.deleted == []

    client.portal.call(run)


def test_session_cloud_rechecks_human_authority_even_for_existing_allocation(warm_case):
    client, topics, actor, cloud = warm_case
    choice = ComputeChoice(name="Cloud", profile="cloud")

    async def run():
        first, _ = await _sessions_for_cloud(client, topics[0])
        async with client.test_request_factory() as db:
            service = MachineService(db, cloud)
            machine = await service.ensure_session_machine(
                first, actor=actor, choice=choice
            )
            await db.commit()
            assert machine.session_id == first
            with pytest.raises(AuthenticationRequiredError):
                await service.ensure_session_machine(
                    first, actor=Actor("agent", None, "agent"), choice=choice
                )
            assert len(cloud.created) + len(cloud.claims) == 1

    client.portal.call(run)


def test_session_migration_preserves_old_vm_and_quota_and_resumes_only_new_one(
    warm_case,
):
    client, topics, actor, cloud = warm_case
    choice = ComputeChoice(name="Cloud", profile="cloud")

    async def run():
        first, _ = await _sessions_for_cloud(client, topics[0])
        async with client.test_request_factory() as db:
            service = MachineService(db, cloud)
            old = await service.ensure_session_machine(
                first, actor=actor, choice=choice
            )
            old_id = old.id
            await service.supersede_session_machine(first, actor=actor)
            await db.commit()
            new = await service.ensure_session_machine(
                first, actor=actor, choice=choice
            )
            assert new.id != old_id
            assert old.session_id == new.session_id == first
            assert old.superseded_at is not None and old.released_at is None
            assert (
                len(
                    await service.quota_machines(
                        await service.quota_team_id(old.project_id)
                    )
                )
                == 2
            )
            assert len(await service.list_active_for_topic(uuid.UUID(topics[0]))) == 2
            new.status = MachineStatus.suspended
            new.last_seen_at = datetime.now(UTC)
            cloud.machines[new.machine_id]["status"] = "suspended"
            await db.commit()
            resumed = await service.ensure_session_machine(
                first, actor=actor, choice=choice
            )
            assert resumed.id == new.id and resumed.status == MachineStatus.resuming
            assert cloud.deleted == []
            assert len(cloud.created) == 1 and len(cloud.claims) == 1

    client.portal.call(run)


def test_a_left_vm_the_leaving_session_failed_to_delete_is_deleted_later(
    warm_case,
):
    client, topics, actor, cloud = warm_case
    choice = ComputeChoice(name="Cloud", profile="cloud")

    async def run():
        first, second = await _sessions_for_cloud(client, topics[0])
        long_ago = datetime.now(UTC) - timedelta(hours=1)
        async with client.test_request_factory() as db:
            service = MachineService(db, cloud)

            async def leave(*, device_id=None, when=None):
                machine = await service.ensure_session_machine(
                    first, actor=actor, choice=choice
                )
                if device_id is not None:
                    machine.device_id = device_id
                await service.supersede_session_machine(first, actor=actor)
                if when is not None:
                    machine.superseded_at = when
                await db.commit()
                return machine

            # Its one delete attempt never landed, and nothing is only on it.
            orphan = await leave(when=long_ago)
            # The other session left without pushing: its work is only here.
            holding = await leave(device_id="holds-unpushed", when=long_ago)
            other = await db.get(AgentSession, second)
            other.execution_request = {
                "retained_leases": [{"device_id": "holds-unpushed"}]
            }
            # The leaving session may still be deleting this one itself.
            just_left = await leave()
            await db.commit()

            assert await service.release_left_machines() == 1
            assert cloud.deleted == [orphan.machine_id]
            for machine in (orphan, holding, just_left):
                await db.refresh(machine)
            assert orphan.released_at is not None
            assert holding.released_at is None and just_left.released_at is None
            assert await service.release_left_machines() == 0

    client.portal.call(run)


def test_a_left_vm_whose_accepted_delete_failed_is_deleted_again(warm_case):
    client, topics, actor, cloud = warm_case
    choice = ComputeChoice(name="Cloud", profile="cloud")

    async def run():
        first, _ = await _sessions_for_cloud(client, topics[0])
        async with client.test_request_factory() as db:
            service = MachineService(db, cloud)

            async def leave():
                machine = await service.ensure_session_machine(
                    first, actor=actor, choice=choice
                )
                await service.supersede_session_machine(first, actor=actor)
                await db.commit()
                await service.release_left_machine(machine.id)
                await db.refresh(machine)
                return machine

            failed = await leave()
            in_flight = await leave()
            assert failed.released_at is not None
            assert cloud.deleted == [failed.machine_id, in_flight.machine_id]
            # The provider took both deletes; this one then failed there.
            failed.status = MachineStatus.error
            await db.commit()

            await service.release_left_machines()
            assert cloud.deleted == [
                failed.machine_id,
                in_flight.machine_id,
                failed.machine_id,
            ]
            await db.refresh(failed)
            assert failed.status == MachineStatus.deleting

    client.portal.call(run)


@pytest.mark.anyio
async def test_cloud_ownership_migration_preserves_legacy_allocation(db_factory):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location(
        "cloud_ownership_migration",
        Path(__file__).parents[2]
        / "alembic/versions/b672a09ef831_cloud_session_ownership.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def exercise(connection):
        connection.exec_driver_sql(
            "CREATE TEMP TABLE project_machines "
            "(id UUID PRIMARY KEY, topic_id UUID, released_at TIMESTAMPTZ)"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_project_machines_active_topic "
            "ON project_machines(topic_id) WHERE released_at IS NULL"
        )
        room, legacy = uuid.uuid4(), uuid.uuid4()
        connection.execute(
            text("INSERT INTO project_machines(id,topic_id) VALUES (:id,:topic)"),
            {"id": legacy, "topic": room},
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        row = connection.exec_driver_sql(
            "SELECT id,session_id FROM project_machines"
        ).one()
        assert row == (legacy, None)
        for _ in range(2):
            connection.execute(
                text(
                    "INSERT INTO project_machines(id,topic_id,session_id) "
                    "VALUES (:id,:topic,:session)"
                ),
                {"id": uuid.uuid4(), "topic": room, "session": uuid.uuid4()},
            )
        assert (
            connection.exec_driver_sql("SELECT count(*) FROM project_machines").scalar()
            == 3
        )
        with pytest.raises(RuntimeError, match="Release session"):
            migration.downgrade()

    async with db_factory() as db:
        await (await db.connection()).run_sync(exercise)
        await db.rollback()


class ClaimCloud(FakeMicroCloud):
    def __init__(self):
        super().__init__()
        self.claims = []
        self.fail_claim = False

    async def claim_warm_machine(self, machine_id, body):
        self.claims.append((machine_id, body))
        if self.fail_claim:
            raise MicroCloudError("response lost")
        return {"id": machine_id, "status": "running", "aiStatus": "disabled"}


def test_central_cloud_compute_enrolls_and_wakes_without_provider_login(
    warm_case, monkeypatch
):
    client, topics, actor, cloud = warm_case
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    # The fixture's warm machine would serve this room; this is the cold path.
    monkeypatch.setattr("app.domain.machine.warm.device_hub.is_online", lambda _: False)

    async def run():
        async with client.test_request_factory() as session:
            service = MachineService(session, cloud)
            machine = await service.ensure_topic_machine(
                uuid.UUID(topics[0]), actor=actor
            )
            assert cloud.created[-1]["aiMode"] == "none"
            machine.status = MachineStatus.running
            machine.ai_mode = "none"
            machine.ai_status = AiStatus.disabled
            machine.ip = "192.0.2.2"
            await session.commit()
            repo = ProjectMachineRepository(session)
            assert machine in await repo.list_awaiting_enrollment(5)
            machine.device_id = "warm-test"
            await session.commit()
            assert (
                uuid.UUID(topics[0]),
                "warm-test",
            ) in await repo.list_ready_topic_devices()

    client.portal.call(lambda: run())


def test_central_warm_creation_does_not_request_subscription(warm_case, monkeypatch):
    client, _, _, cloud = warm_case
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")

    async def run():
        async with client.test_request_factory() as session:
            await WarmPoolService(session, cloud)._new()
            rows = (
                await session.scalars(
                    select(WarmMachine).where(WarmMachine.state == "preparing")
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].create_request["aiMode"] == "none"

    client.portal.call(lambda: run())


def test_failed_cloud_creation_is_failed_environment_not_permanent_pending(warm_case):
    client, topics, actor, cloud = warm_case

    async def run():
        async with client.test_request_factory() as session:
            machine = await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(topics[1]), actor=actor
            )
            machine.device_id = None
            machine.status = MachineStatus.error
            await session.commit()
            # Remove the fixture binding to exercise a failed cold enrollment.
            from app.domain.device.models import DeviceTopicRow

            binding = await session.scalar(
                select(DeviceTopicRow).where(
                    DeviceTopicRow.topic_id == uuid.UUID(topics[1])
                )
            )
            if binding is not None:
                await session.delete(binding)
                await session.commit()

    client.portal.call(lambda: run())
    token = seed_user(client, "owner")
    topic = client.get(f"/topics/{topics[1]}").json()["data"]
    response = client.get(
        f"/projects/{topic['project_id']}/environment/rooms/{topics[1]}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == "failed"


def test_a_cloud_machine_still_being_created_is_a_room_that_really_is_preparing(
    warm_case,
):
    """「正在准备」的那一半，钉在这里，因为另一半刚刚不再这么说。

    没有设备绑定的房间现在回 `unbound`——除非有台云机器正在建起来，那是真的在
    准备，说它在准备并不是假话。"""
    client, topics, actor, cloud = warm_case

    async def run():
        async with client.test_request_factory() as session:
            machine = await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(topics[1]), actor=actor
            )
            machine.device_id = None
            machine.status = MachineStatus.provisioning
            await session.commit()
            from app.domain.device.models import DeviceTopicRow

            binding = await session.scalar(
                select(DeviceTopicRow).where(
                    DeviceTopicRow.topic_id == uuid.UUID(topics[1])
                )
            )
            if binding is not None:
                await session.delete(binding)
                await session.commit()

    client.portal.call(lambda: run())
    token = seed_user(client, "owner")
    topic = client.get(f"/topics/{topics[1]}").json()["data"]
    response = client.get(
        f"/projects/{topic['project_id']}/environment/rooms/{topics[1]}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == "pending"


@pytest.mark.parametrize(
    "supply,direct,center,internal,expected",
    [
        (Supply.cloud, True, None, None, "http://127.0.0.1:18080"),
        (Supply.cloud, False, None, None, "https://public.example/api"),
        (Supply.self_hosted, True, None, None, "https://public.example/api"),
        (
            Supply.self_hosted,
            False,
            "warm-test",
            "http://127.0.0.1:18081/",
            "http://127.0.0.1:18081",
        ),
        (
            Supply.self_hosted,
            False,
            "other-device",
            "http://127.0.0.1:18081",
            "https://public.example/api",
        ),
    ],
)
def test_device_api_route_follows_cloud_enrollment(
    warm_case, monkeypatch, supply, direct, center, internal, expected
):
    monkeypatch.setattr(settings, "agent_session_device_id", center)
    monkeypatch.setattr(settings, "agent_session_api_base", internal)
    client, _, _, _ = warm_case

    async def run():
        async with client.test_request_factory() as session:
            device = await session.get(DeviceRow, "warm-test")
            device.supply = supply
            device.cloud_control_private = direct
            await session.commit()
        channel = DeviceChannel(
            session_factory=client.test_request_factory,
            public_base="https://public.example/api",
        )
        assert await channel._device_api_base("warm-test") == expected

    client.portal.call(lambda: run())


@pytest.fixture
def warm_case(client, monkeypatch):
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    monkeypatch.setattr("app.domain.machine.warm.device_hub.is_online", lambda _: True)
    token = seed_user(client, "owner")
    project = _project(client, {"Authorization": f"Bearer {token}"})
    topics = [
        client.post(
            "/topics",
            json={"project_id": project, "title": title},
        ).json()["data"]["id"]
        for title in ("One", "Two")
    ]

    async def seed():
        async with client.test_request_factory() as session:
            user = await UserRepository(session).get_by_handle("owner")
            owner = await IdentityService(session).ensure_agent_user(
                handle="cheese-warm-pool"
            )
            device = DeviceRow(
                device_id="warm-test",
                name="Unused",
                token="test-warm-token",
                owner_user_id=owner.id,
                supply=Supply.cloud,
                created_at=datetime.now(UTC),
            )
            session.add(device)
            session.add(
                WarmMachine(
                    state="ready",
                    machine_id=200,
                    device_id=device.device_id,
                    enrolled_at=datetime.now(UTC),
                    ip="192.0.2.1",
                    create_request={
                        "hostname": "warm-test",
                        "offeringId": 1,
                        "cores": settings.microcloud_default_cores,
                        "memoryMb": settings.microcloud_default_memory_mb,
                        "diskGb": settings.microcloud_default_disk_gb,
                        "user": settings.microcloud_login_user,
                        "aiMode": "none",
                    },
                )
            )
            await session.commit()
            return Actor("owner", user.id, "token")

    actor = client.portal.call(lambda: seed())
    return client, topics, actor, ClaimCloud()


def test_cloud_execution_requires_an_active_machine_in_the_project(warm_case):
    client, topics, actor, cloud = warm_case

    async def run():
        async with client.test_request_factory() as db:
            project_id = (await db.get(Topic, uuid.UUID(topics[0]))).project_id
            assert not await machine_owner_reads.active_cloud_device_for_project(
                db, "warm-test", project_id
            )
            machine = await MachineService(db, cloud).ensure_topic_machine(
                uuid.UUID(topics[0]), actor=actor
            )
            assert await machine_owner_reads.active_cloud_device_for_project(
                db, "warm-test", project_id
            )
            assert not await machine_owner_reads.active_cloud_device_for_project(
                db, "warm-test", uuid.uuid4()
            )
            machine.superseded_at = datetime.now(UTC)
            await db.flush()
            assert not await machine_owner_reads.active_cloud_device_for_project(
                db, "warm-test", project_id
            )
            machine.superseded_at = None
            machine.released_at = datetime.now(UTC)
            await db.flush()
            assert not await machine_owner_reads.active_cloud_device_for_project(
                db, "warm-test", project_id
            )

    client.portal.call(run)


def test_concurrent_rooms_take_one_warm_machine_and_create_one_cold(warm_case):
    client, topics, actor, cloud = warm_case

    async def ensure(topic):
        async with client.test_request_factory() as session:
            machine = await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(topic), actor=actor
            )
            await session.commit()
            return machine.machine_id, machine.device_id

    async def run():
        return await asyncio.gather(
            ensure(topics[0]), ensure(topics[0]), ensure(topics[1])
        )

    results = client.portal.call(lambda: run())
    assert results[0] == results[1]
    assert len({result[0] for result in results}) == 2
    assert len(cloud.claims) == 1
    assert len(cloud.created) == 1
    assert (200, "warm-test") in results


def test_second_admission_waits_for_the_claim_without_deadlock(warm_case):
    """A room admitted again while its warm claim is still at the provider
    waits for that claim holding no database lock, then sees the claimed
    machine; the provider is still asked once."""
    client, topics, actor, cloud = warm_case
    in_flight = asyncio.Event()
    release = asyncio.Event()
    original = cloud.claim_warm_machine

    async def slow_claim(machine_id, body):
        in_flight.set()
        await release.wait()
        return await original(machine_id, body)

    cloud.claim_warm_machine = slow_claim

    async def ensure():
        async with client.test_request_factory() as session:
            machine = await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(topics[0]), actor=actor
            )
            await session.commit()
            return machine.machine_id, machine.device_id

    async def run():
        first = asyncio.create_task(ensure())
        await asyncio.wait_for(in_flight.wait(), timeout=5)
        second = asyncio.create_task(ensure())
        await asyncio.sleep(0.3)  # the second is now waiting for the claim
        release.set()
        return await asyncio.wait_for(asyncio.gather(first, second), timeout=10)

    results = client.portal.call(lambda: run())
    assert results[0] == results[1] == (200, "warm-test")
    assert len(cloud.claims) == 1


@pytest.mark.parametrize("session_owned", [False, True])
def test_timeout_keeps_quota_reserved_and_retry_finishes_same_claim(
    warm_case, monkeypatch, session_owned
):
    client, topics, actor, cloud = warm_case
    monkeypatch.setattr(
        "app.domain.machine.services.get_machine_limit", AsyncMock(return_value=1)
    )
    cloud.fail_claim = True

    async def run():
        sessions = []
        if session_owned:
            # One session in each room: rooms, not sessions, rent machines.
            [first, _] = await _sessions_for_cloud(client, topics[0])
            async with client.test_request_factory() as db:
                other = AgentSession(
                    topic_id=uuid.UUID(topics[1]),
                    agent_handle="cloud-a",
                    harness="claude-code",
                )
                db.add(other)
                await db.commit()
                sessions = [first, other.id]

        async def ensure(db, index):
            service = MachineService(db, cloud)
            if session_owned:
                return await service.ensure_session_machine(
                    sessions[index],
                    actor=actor,
                    choice=ComputeChoice(name="Cloud", profile="cloud"),
                )
            return await service.ensure_topic_machine(
                uuid.UUID(topics[index]), actor=actor
            )

        async with client.test_request_factory() as session:
            first = await ensure(session, 0)
            assert first.device_id is None
        async with client.test_request_factory() as session:
            with pytest.raises(ValidationError, match="1 / 1"):
                await ensure(session, 1)
        cloud.fail_claim = False
        async with client.test_request_factory() as session:
            retried = await ensure(session, 0)

            assert retried.machine_id == 200
            assert retried.device_id == "warm-test"
            assert len((await session.scalars(select(ProjectMachine))).all()) == 1
            assert len((await session.scalars(select(DeviceTeamRow))).all()) == 1

    client.portal.call(lambda: run())
    assert cloud.claims[0] == cloud.claims[1]
    assert cloud.created == []


def test_nonmember_cannot_claim_and_unassigned_device_has_no_team(warm_case):
    client, topics, _, cloud = warm_case

    async def run():
        async with client.test_request_factory() as session:
            assert (await session.scalars(select(DeviceTeamRow))).all() == []
            with pytest.raises(ForbiddenError):
                await MachineService(session, cloud).ensure_topic_machine(
                    uuid.UUID(topics[0]),
                    actor=Actor("outsider", 999999, "token"),
                )

    client.portal.call(lambda: run())
    assert cloud.claims == []
    assert cloud.created == []


@pytest.mark.parametrize("direct", [False, True])
def test_background_preparation_waits_for_ai_then_connects_before_ready(
    warm_case, monkeypatch, direct
):
    client, _, _, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 1)
    monkeypatch.setattr(settings, "microcloud_direct_control", direct)
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")
    bootstrap = AsyncMock(return_value="Connected.")
    monkeypatch.setattr("app.domain.machine.warm.enrollment.run_bootstrap", bootstrap)

    async def run():
        async with client.test_request_factory() as session:
            row = await session.scalar(select(WarmMachine))
            row.state = "preparing"
            row.machine_id = None
            row.enrolled_at = None
            row.bootstrap_key = "test-bootstrap"
            row.create_request = {
                **row.create_request,
                "customerId": 7,
                "accountId": 9,
                "warmPoolKey": str(row.id),
            }
            await session.commit()
            service = WarmPoolService(session, cloud)
            await service.sweep()
            assert row.state == "preparing"
            bootstrap.assert_not_called()
            cloud.machines[row.machine_id].update(
                status="running", aiStatus="disabled", ip="192.0.2.1"
            )
            await service.sweep()
            assert row.state == "ready"
            assert row.bootstrap_key is None
            assert row.enrolled_at is not None
            assert (await session.scalars(select(DeviceTeamRow))).all() == []

        async with client.test_request_factory() as session:
            device = await session.get(DeviceRow, "warm-test")
            assert device.cloud_control_private is direct

    client.portal.call(lambda: run())
    bootstrap.assert_awaited_once()


def test_disabling_pool_deletes_only_unused_capacity(warm_case, monkeypatch):
    client, _, _, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 0)

    async def run():
        async with client.test_request_factory() as session:
            await WarmPoolService(session, cloud).sweep()
            row = await session.scalar(select(WarmMachine))
            assert row.state == "deleted"
            assert await session.get(DeviceRow, "warm-test") is None

    client.portal.call(lambda: run())
    assert cloud.deleted == [200]


def test_failed_claim_stays_reserved_until_explicit_retry(warm_case, monkeypatch):
    client, topics, actor, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 1)
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")
    cloud.fail_claim = True

    async def run():
        async with client.test_request_factory() as session:
            machine = await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(topics[0]), actor=actor
            )
            service = WarmPoolService(session, cloud)
            for _ in range(4):
                await service.finish_claim(machine)
            row = await session.scalar(select(WarmMachine))
            assert row.state == "claim_failed"
            assert machine.warm_claim_pending
            assert machine.enroll_error
            # An unconfirmed handoff may still bill the platform; do not replace it.
            before = len(cloud.created)
            await service.sweep()
            assert len(cloud.created) == before
            assert len((await session.scalars(select(WarmMachine))).all()) == 1
            cloud.fail_claim = False
            await service.finish_claim(machine)
            assert row.state == "claimed"
            assert not machine.warm_claim_pending

    client.portal.call(lambda: run())
    assert cloud.created == []


def _failed_cleanup(machine_id, error="delete HTTP None"):
    return WarmMachine(
        state="cleanup_failed",
        machine_id=machine_id,
        attempts=5,
        error=error,
        create_request={"hostname": f"warm-stale-{machine_id}"},
    )


def _provider_still_has(cloud, *machine_ids):
    for machine_id in machine_ids:
        cloud.machines[machine_id] = {"id": machine_id, "status": "error"}


def test_machines_that_failed_cleanup_do_not_hold_the_pool_empty(
    warm_case, monkeypatch
):
    client, topics, actor, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 2)
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")

    async def run():
        async with client.test_request_factory() as session:
            # The one ready machine goes to a room; two stale cleanups remain.
            await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(topics[0]), actor=actor
            )
            _provider_still_has(cloud, 901, 902)
            session.add_all([_failed_cleanup(901), _failed_cleanup(902)])
            await session.commit()
            await WarmPoolService(session, cloud).sweep()
            states = (await session.scalars(select(WarmMachine.state))).all()
            assert states.count("preparing") == 1
            # A record that may still be billed is left exactly as it was.
            assert states.count("cleanup_failed") == 2
            assert cloud.deleted == []

    client.portal.call(lambda: run())


def test_a_pile_of_failed_cleanups_stops_replacement(warm_case, monkeypatch):
    client, topics, actor, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 2)
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")

    async def run():
        async with client.test_request_factory() as session:
            await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(topics[0]), actor=actor
            )
            _provider_still_has(cloud, 901, 902, 903)
            session.add_all([_failed_cleanup(n) for n in (901, 902, 903)])
            await session.commit()
            await WarmPoolService(session, cloud).sweep()
            states = (await session.scalars(select(WarmMachine.state))).all()
            assert "preparing" not in states

    client.portal.call(lambda: run())


def test_failed_cleanups_the_provider_has_since_dropped_stop_blocking_the_pool(
    warm_case, monkeypatch
):
    client, topics, actor, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 2)
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")

    async def run():
        async with client.test_request_factory() as session:
            await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(topics[0]), actor=actor
            )
            # The provider no longer knows 901-903; nothing is left to bill.
            session.add_all([_failed_cleanup(n) for n in (901, 902, 903)])
            # 904 was set aside by a person; it is not ours to settle.
            session.add(
                _failed_cleanup(
                    904, error="Quarantined stale deletion: ownership unverified"
                )
            )
            await session.commit()
            await WarmPoolService(session, cloud).sweep()
            rows = {
                row.machine_id: row.state
                for row in (await session.scalars(select(WarmMachine))).all()
                if row.machine_id in (901, 902, 903, 904)
            }
            assert rows == {
                901: "deleted",
                902: "deleted",
                903: "deleted",
                904: "cleanup_failed",
            }
            states = (await session.scalars(select(WarmMachine.state))).all()
            assert states.count("preparing") == 1
            assert cloud.deleted == []

    client.portal.call(lambda: run())


def _deleting(machine_id, since):
    return WarmMachine(
        state="deleting",
        machine_id=machine_id,
        create_request={"hostname": f"warm-deleting-{machine_id}"},
        updated_at=since,
    )


def _provider_keeps_what_it_deletes(cloud, monkeypatch, *machine_ids):
    """A provider that accepts every deletion and never carries one out."""
    for machine_id in machine_ids:
        cloud.machines[machine_id] = {"id": machine_id, "status": "error"}

    async def accept(machine_id):
        cloud.deleted.append(machine_id)

    monkeypatch.setattr(cloud, "delete_machine", accept)


def test_a_machine_the_provider_never_deletes_stops_holding_the_pool(
    warm_case, monkeypatch
):
    client, topics, actor, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 1)
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")
    _provider_keeps_what_it_deletes(cloud, monkeypatch, 901)

    async def run():
        async with client.test_request_factory() as session:
            await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(topics[0]), actor=actor
            )
            session.add(_deleting(901, datetime.now(UTC) - timedelta(hours=8)))
            await session.commit()
            await WarmPoolService(session, cloud).sweep()
            states = (await session.scalars(select(WarmMachine.state))).all()
            assert "deleting" not in states
            # Still a record of something that may be billed, for a person.
            assert states.count("cleanup_failed") == 1
            assert states.count("preparing") == 1

    client.portal.call(lambda: run())


def test_a_deletion_still_under_way_keeps_its_place(warm_case, monkeypatch):
    client, topics, actor, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 1)
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")
    _provider_keeps_what_it_deletes(cloud, monkeypatch, 902)

    async def run():
        async with client.test_request_factory() as session:
            await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(topics[0]), actor=actor
            )
            session.add(_deleting(902, datetime.now(UTC) - timedelta(minutes=1)))
            await session.commit()
            await WarmPoolService(session, cloud).sweep()
            states = (await session.scalars(select(WarmMachine.state))).all()
            assert states.count("deleting") == 1
            assert "preparing" not in states

    client.portal.call(lambda: run())


async def _startup_lines(client, topic_id) -> list[str]:
    async with client.test_request_factory() as db:
        blocks = await BlockRepository(db).list_for_topic(uuid.UUID(topic_id))
        return [
            str(block.content)
            for block in blocks
            if (block.meta or {}).get("event_type") == "cloud_startup"
        ]


@pytest.fixture
def cold_case(warm_case, monkeypatch):
    """Rooms that rent cold machines, with their startup lines recorded."""
    monkeypatch.setattr("app.domain.machine.warm.device_hub.is_online", lambda _: False)
    client = warm_case[0]
    monkeypatch.setattr(progress, "async_session_factory", client.test_request_factory)
    return warm_case


async def _fail(service, cloud, machine):
    """MicroCloud gives up building the machine, and the sweep reads that."""
    cloud.machines[machine.machine_id]["status"] = "error"
    await service.refresh_due()
    await service._session.commit()


def test_a_machine_the_provider_failed_to_build_is_replaced(cold_case):
    """dev, 2026-10-01: a Proxmox create outlived MicroCloud's wait, the machine
    was left `error`, and every later turn of the room got the same failure."""
    client, topics, actor, cloud = cold_case
    choice = ComputeChoice(name="Cloud", profile="cloud")

    async def run():
        session_id, _ = await _sessions_for_cloud(client, topics[0])
        async with client.test_request_factory() as db:
            service = MachineService(db, cloud)
            failed = await service.ensure_session_machine(
                session_id, actor=actor, choice=choice
            )
            await db.commit()
            await _fail(service, cloud, failed)

            fresh = await service.ensure_session_machine(
                session_id, actor=actor, choice=choice
            )
            await db.commit()
            assert fresh.machine_id != failed.machine_id
            assert cloud.deleted == [failed.machine_id]

            cloud.machines[fresh.machine_id].update(
                status="running", ip="192.0.2.9", aiStatus="disabled"
            )
            await service.refresh_due()
            await db.commit()
            again = await service.ensure_session_machine(
                session_id, actor=actor, choice=choice
            )
            assert (again.id, again.status) == (fresh.id, MachineStatus.running)
            assert len(cloud.created) == 2

    client.portal.call(run)
    lines = client.portal.call(_startup_lines, client, topics[0])
    assert any("改为申请第 2 台" in line for line in lines), lines
    assert not any("不再自动申请" in line for line in lines), lines


def test_a_room_whose_machines_keep_failing_stops_and_says_it_retried(cold_case):
    client, topics, actor, cloud = cold_case
    choice = ComputeChoice(name="Cloud", profile="cloud")

    async def run():
        session_id, _ = await _sessions_for_cloud(client, topics[0])
        async with client.test_request_factory() as db:
            service = MachineService(db, cloud)
            rented = []
            for _ in range(MAX_PROVIDER_ERRORS):
                machine = await service.ensure_session_machine(
                    session_id, actor=actor, choice=choice
                )
                await db.commit()
                rented.append(machine.machine_id)
                await _fail(service, cloud, machine)
            with pytest.raises(CloudKeepsFailing) as failing:
                await service.ensure_session_machine(
                    session_id, actor=actor, choice=choice
                )
            await db.commit()
            # A later turn within the hour is told the same, renting nothing.
            with pytest.raises(CloudKeepsFailing):
                await service.ensure_session_machine(
                    session_id, actor=actor, choice=choice
                )
            return str(failing.value), rented

    message, rented = client.portal.call(run)
    assert f"连续 {MAX_PROVIDER_ERRORS} 次" in message and "重试" in message
    assert len(cloud.created) == MAX_PROVIDER_ERRORS
    assert sorted(cloud.deleted) == sorted(rented)
    lines = client.portal.call(_startup_lines, client, topics[0])
    assert sum("不再自动申请" in line for line in lines) == 1, lines


def test_an_enrolled_machine_in_error_is_not_deleted(cold_case):
    """The room's unpushed work may be on it, so it is reported, not replaced."""
    client, topics, actor, cloud = cold_case
    choice = ComputeChoice(name="Cloud", profile="cloud")

    async def run():
        session_id, _ = await _sessions_for_cloud(client, topics[0])
        async with client.test_request_factory() as db:
            service = MachineService(db, cloud)
            machine = await service.ensure_session_machine(
                session_id, actor=actor, choice=choice
            )
            machine.device_id = "enrolled-before-error"
            await db.commit()
            await _fail(service, cloud, machine)
            kept = await service.ensure_session_machine(
                session_id, actor=actor, choice=choice
            )
            assert kept.id == machine.id

    client.portal.call(run)
    assert cloud.deleted == []
    assert len(cloud.created) == 1
