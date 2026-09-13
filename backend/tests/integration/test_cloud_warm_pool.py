"""Room admission and recovery over real database transactions."""

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import ForbiddenError, ValidationError
from app.domain.agent.device_provider import DeviceChannel
from app.domain.device.models import DeviceRow, DeviceTeamRow
from app.domain.device.supply import Supply, Visibility
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.machine.microcloud import MicroCloudError
from app.domain.machine.models import (
    AiStatus,
    MachineStatus,
    ProjectMachine,
    WarmMachine,
)
from app.domain.machine.repositories import ProjectMachineRepository
from app.domain.machine.services import MachineService
from app.domain.machine.warm import WarmPoolService
from app.domain.user.repositories import UserRepository
from tests.conftest import seed_user
from tests.integration.test_project_machines import _project
from tests.unit.test_machine_service import FakeMicroCloud


class ClaimCloud(FakeMicroCloud):
    def __init__(self):
        super().__init__()
        self.claims = []
        self.fail_claim = False

    async def claim_warm_machine(self, machine_id, body):
        self.claims.append((machine_id, body))
        if self.fail_claim:
            raise MicroCloudError("response lost")
        return {"id": machine_id, "status": "running", "aiStatus": "ready"}


def test_central_cloud_compute_enrolls_and_wakes_without_provider_login(
    warm_case, monkeypatch
):
    client, topics, actor, cloud = warm_case
    monkeypatch.setattr(settings, "agent_session_device_id", "center")

    async def run():
        async with client.test_factory() as session:
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
            assert machine in await repo.list_awaiting_enrollment(
                5, desired_ai_mode="none"
            )
            assert await service.reconcile_ai_mode() == 0
            assert cloud.ai_switches == []
            machine.device_id = "warm-test"
            await session.commit()
            assert (
                uuid.UUID(topics[0]),
                "warm-test",
            ) in await repo.list_ready_topic_devices()

    asyncio.run(run())


def test_central_warm_creation_does_not_request_subscription(warm_case, monkeypatch):
    client, _, _, cloud = warm_case
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")

    async def run():
        async with client.test_factory() as session:
            await WarmPoolService(session, cloud)._new()
            rows = (
                await session.scalars(
                    select(WarmMachine).where(WarmMachine.state == "preparing")
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].create_request["aiMode"] == "none"

    asyncio.run(run())


def test_failed_cloud_creation_is_failed_environment_not_permanent_pending(warm_case):
    client, topics, actor, cloud = warm_case

    async def run():
        async with client.test_factory() as session:
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

    asyncio.run(run())
    token = seed_user(client, "owner")
    topic = client.get(f"/topics/{topics[1]}").json()["data"]
    response = client.get(
        f"/projects/{topic['project_id']}/environment/rooms/{topics[1]}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == "failed"


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
        async with client.test_factory() as session:
            device = await session.get(DeviceRow, "warm-test")
            device.supply = supply
            device.cloud_control_private = direct
            await session.commit()
        channel = DeviceChannel(
            session_factory=client.test_factory,
            public_base="https://public.example/api",
        )
        assert await channel._device_api_base("warm-test") == expected

    asyncio.run(run())


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
            json={"project_id": project, "title": title, "created_by": "owner"},
        ).json()["data"]["id"]
        for title in ("One", "Two")
    ]

    async def seed():
        async with client.test_factory() as session:
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
                visibility=Visibility.host,
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
                        "aiMode": settings.microcloud_ai_mode,
                    },
                )
            )
            await session.commit()
            return Actor("owner", user.id, False, "token")

    actor = asyncio.run(seed())
    return client, topics, actor, ClaimCloud()


def test_concurrent_rooms_take_one_warm_machine_and_create_one_cold(warm_case):
    client, topics, actor, cloud = warm_case

    async def ensure(topic):
        async with client.test_factory() as session:
            machine = await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(topic), actor=actor
            )
            await session.commit()
            return machine.machine_id, machine.device_id

    async def run():
        return await asyncio.gather(
            ensure(topics[0]), ensure(topics[0]), ensure(topics[1])
        )

    results = asyncio.run(run())
    assert results[0] == results[1]
    assert len({result[0] for result in results}) == 2
    assert len(cloud.claims) == 1
    assert len(cloud.created) == 1
    assert (200, "warm-test") in results


def test_timeout_keeps_quota_reserved_and_retry_finishes_same_claim(
    warm_case, monkeypatch
):
    client, topics, actor, cloud = warm_case
    monkeypatch.setattr(
        "app.domain.machine.services.get_machine_limit", AsyncMock(return_value=1)
    )
    cloud.fail_claim = True

    async def run():
        async with client.test_factory() as session:
            first = await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(topics[0]), actor=actor
            )
            assert first.device_id is None
        async with client.test_factory() as session:
            with pytest.raises(ValidationError, match="1 / 1"):
                await MachineService(session, cloud).ensure_topic_machine(
                    uuid.UUID(topics[1]), actor=actor
                )
        cloud.fail_claim = False
        async with client.test_factory() as session:
            retried = await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(topics[0]), actor=actor
            )
            assert retried.machine_id == 200
            assert retried.device_id == "warm-test"
            assert len((await session.scalars(select(ProjectMachine))).all()) == 1
            assert len((await session.scalars(select(DeviceTeamRow))).all()) == 1

    asyncio.run(run())
    assert cloud.claims[0] == cloud.claims[1]
    assert cloud.created == []


def test_nonmember_cannot_claim_and_unassigned_device_has_no_team(warm_case):
    client, topics, _, cloud = warm_case

    async def run():
        async with client.test_factory() as session:
            assert (await session.scalars(select(DeviceTeamRow))).all() == []
            with pytest.raises(ForbiddenError):
                await MachineService(session, cloud).ensure_topic_machine(
                    uuid.UUID(topics[0]),
                    actor=Actor("outsider", 999999, False, "token"),
                )

    asyncio.run(run())
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
        async with client.test_factory() as session:
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
                status="running", aiStatus="ready", ip="192.0.2.1"
            )
            await service.sweep()
            assert row.state == "ready"
            assert row.bootstrap_key is None
            assert row.enrolled_at is not None
            assert (await session.scalars(select(DeviceTeamRow))).all() == []

        async with client.test_factory() as session:
            device = await session.get(DeviceRow, "warm-test")
            assert device.cloud_control_private is direct

    asyncio.run(run())
    bootstrap.assert_awaited_once()


def test_disabling_pool_deletes_only_unused_capacity(warm_case, monkeypatch):
    client, _, _, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 0)

    async def run():
        async with client.test_factory() as session:
            await WarmPoolService(session, cloud).sweep()
            row = await session.scalar(select(WarmMachine))
            assert row.state == "deleted"
            assert await session.get(DeviceRow, "warm-test") is None

    asyncio.run(run())
    assert cloud.deleted == [200]


def test_failed_claim_stays_reserved_until_explicit_retry(warm_case, monkeypatch):
    client, topics, actor, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 1)
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")
    cloud.fail_claim = True

    async def run():
        async with client.test_factory() as session:
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

    asyncio.run(run())
    assert cloud.created == []
