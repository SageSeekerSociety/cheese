"""The warm pool: unused machines prepared ahead of demand, and cleaned up
when they are not wanted or go wrong.

Taking a warm machine into the host pool is covered with placement
(``test_cloud_host_pool.py``).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.agent.device_provider import DeviceChannel
from app.domain.device.models import DeviceRow, DeviceTeamRow
from app.domain.device.supply import Supply
from app.domain.identity.services import IdentityService
from app.domain.machine.models import WarmMachine
from app.domain.machine.warm import WarmPoolService
from tests.microcloud import FakeMicroCloud


def test_central_warm_creation_does_not_request_subscription(warm_case, monkeypatch):
    client, cloud = warm_case
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
    client, _ = warm_case

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
    """One ready warm machine, enrolled and connected."""
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    monkeypatch.setattr("app.domain.machine.warm.device_hub.is_online", lambda _: True)

    async def seed():
        async with client.test_request_factory() as session:
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

    client.portal.call(lambda: seed())
    return client, FakeMicroCloud()


async def _taken_by_the_pool(session) -> None:
    """The ready machine became a host of the pool."""
    row = await session.scalar(select(WarmMachine).where(WarmMachine.state == "ready"))
    row.state = "claimed"
    await session.commit()


@pytest.mark.parametrize("direct", [False, True])
def test_background_preparation_waits_for_ai_then_connects_before_ready(
    warm_case, monkeypatch, direct
):
    client, cloud = warm_case
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
    client, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 0)

    async def run():
        async with client.test_request_factory() as session:
            await WarmPoolService(session, cloud).sweep()
            row = await session.scalar(select(WarmMachine))
            assert row.state == "deleted"
            assert await session.get(DeviceRow, "warm-test") is None

    client.portal.call(lambda: run())
    assert cloud.deleted == [200]


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
    client, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 2)
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")

    async def run():
        async with client.test_request_factory() as session:
            # The one ready machine goes to a room; two stale cleanups remain.
            await _taken_by_the_pool(session)
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
    client, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 2)
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")

    async def run():
        async with client.test_request_factory() as session:
            await _taken_by_the_pool(session)
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
    client, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 2)
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")

    async def run():
        async with client.test_request_factory() as session:
            await _taken_by_the_pool(session)
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
    client, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 1)
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")
    _provider_keeps_what_it_deletes(cloud, monkeypatch, 901)

    async def run():
        async with client.test_request_factory() as session:
            await _taken_by_the_pool(session)
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
    client, cloud = warm_case
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 1)
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")
    _provider_keeps_what_it_deletes(cloud, monkeypatch, 902)

    async def run():
        async with client.test_request_factory() as session:
            await _taken_by_the_pool(session)
            session.add(_deleting(902, datetime.now(UTC) - timedelta(minutes=1)))
            await session.commit()
            await WarmPoolService(session, cloud).sweep()
            states = (await session.scalars(select(WarmMachine.state))).all()
            assert states.count("deleting") == 1
            assert "preparing" not in states

    client.portal.call(lambda: run())
