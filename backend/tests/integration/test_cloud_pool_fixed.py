"""The pool at a fixed size, and the rolling rebuild of its hosts.

With ``cloud_pool_fixed_hosts`` the pool is that many hosts whatever the
demand: it adds them without waiting for a session, keeps an idle one instead
of releasing it, and releases the ones above its size once they are idle. A
host no longer made of what a host created now would be — its image version is
different, or it is older than ``cloud_host_rebuild_after_days`` — is rolled:
one at a time, out of rotation, and released when its last sandbox is gone, so
a rebuild never costs the pool a slot or interrupts a running sandbox.

The hosts, the provider and the sessions are the ones of
``test_cloud_host_pool``; a host here is put on the rows by hand, so a test can
say what it was made of and when.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from sqlalchemy import delete, select

from app.core.config import settings
from app.domain.machine import enrollment
from app.domain.machine.models import AiStatus, CloudHost, MachineStatus, SandboxHome
from tests.integration.conftest import session_auth_headers
from tests.integration.test_cloud_host_pool import Case
from tests.integration.test_cloud_host_pool import pool as pool


def _current_version(case: Case) -> str:
    """What a host created now would record."""

    async def go():
        return await enrollment.current_host_image_version(case.cloud)

    return case.run(go)


def _hosts(case: Case) -> list[CloudHost]:
    async def go():
        async with case.client.test_request_factory() as db:
            return list(
                await db.scalars(
                    select(CloudHost).order_by(CloudHost.created_at, CloudHost.id)
                )
            )

    return case.run(go)


def _edit(case: Case, host_id: uuid.UUID, **fields) -> None:
    async def go():
        async with case.client.test_request_factory() as db:
            row = await db.get(CloudHost, host_id)
            for name, value in fields.items():
                setattr(row, name, value)
            await db.commit()

    case.run(go)


def _empty(case: Case, host_id: uuid.UUID) -> None:
    """The sandboxes on the host are gone, as the idle sweep leaves them."""

    async def go():
        async with case.client.test_request_factory() as db:
            await db.execute(delete(SandboxHome).where(SandboxHome.host_id == host_id))
            await db.commit()

    case.run(go)


#: Provider machine ids for the hosts a test puts on the rows itself, so two
#: of them never claim the same machine.
_SEEDED = {"machine": 9000}


def _seed_host(
    case: Case,
    name: str,
    *,
    image_version: str,
    created_at: datetime | None = None,
) -> uuid.UUID:
    """A host the sweep finds running and enrolled, its connector up, with the
    size a host created here gets."""

    host_id = uuid.uuid4()
    device_id = f"dev-{name}"
    _SEEDED["machine"] += 1
    machine_id = _SEEDED["machine"]

    async def go():
        async with case.client.test_request_factory() as db:
            db.add(
                CloudHost(
                    id=host_id,
                    machine_id=machine_id,
                    customer_id=1,
                    account_id=1,
                    offering_id=1,
                    hostname=name,
                    login_user="cheese",
                    cores=settings.microcloud_default_cores,
                    memory_mb=settings.microcloud_default_memory_mb,
                    disk_gb=settings.microcloud_default_disk_gb,
                    sandbox_disk_gb=settings.microcloud_default_disk_gb,
                    status=MachineStatus.running,
                    ai_status=AiStatus.disabled,
                    device_id=device_id,
                    enrolled_at=datetime.now(UTC),
                    created_at=created_at or datetime.now(UTC),
                    image_version=image_version,
                )
            )
            await db.commit()

    case.run(go)
    case.online.add(device_id)
    return host_id


def _draining(case: Case) -> list[CloudHost]:
    return [
        host
        for host in _hosts(case)
        if host.draining_since is not None and host.released_at is None
    ]


def test_a_fixed_pool_fills_to_its_size_without_a_session(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 3)

    pool.pool("maintain")

    assert len(pool.cloud.created) == 3
    assert len(_hosts(pool)) == 3

    pool.pool("maintain")

    assert len(pool.cloud.created) == 3


def test_a_fixed_pool_keeps_an_idle_host_past_the_hold(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 1)
    [alice] = pool.room("alice", 1)
    host_id = pool.place("alice", alice)
    pool.up(host_id)
    _empty(pool, host_id)

    pool.pool("maintain")
    assert pool.host(host_id).released_at is None

    _edit(
        pool,
        host_id,
        idle_since=datetime.now(UTC)
        - timedelta(seconds=settings.cloud_host_idle_hold_s + 1),
    )
    pool.pool("maintain")

    assert pool.host(host_id).released_at is None
    assert pool.cloud.deleted == []


def test_a_fixed_pool_releases_the_hosts_above_its_size(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 3)
    pool.pool("maintain")
    assert len(pool.cloud.created) == 3
    for host in _hosts(pool):
        _edit(pool, host.id, idle_since=datetime.now(UTC) - timedelta(days=1))
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 2)

    pool.pool("maintain")

    live = [host for host in _hosts(pool) if host.released_at is None]
    assert len(live) == 2
    assert len(pool.cloud.deleted) == 1
    assert len(pool.cloud.created) == 3


def test_a_new_host_records_what_it_was_made_of(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 1)

    pool.pool("maintain")

    [host] = _hosts(pool)
    assert host.image_version == _current_version(pool)


def test_an_out_of_date_host_is_taken_out_of_rotation(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 2)
    current = _current_version(pool)
    old = _seed_host(
        pool,
        "host-old",
        image_version="stale",
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    young = _seed_host(pool, "host-young", image_version=current)

    pool.pool("maintain")

    assert pool.host(old).draining_since is not None
    assert pool.host(young).draining_since is None


def test_a_draining_host_takes_no_new_sandbox(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 2)
    old = _seed_host(
        pool,
        "host-old",
        image_version="stale",
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    young = _seed_host(pool, "host-young", image_version=_current_version(pool))
    pool.pool("maintain")
    assert pool.host(old).draining_since is not None

    [alice] = pool.room("alice", 1)

    assert pool.place("alice", alice) == young


def test_a_draining_host_is_released_when_its_last_sandbox_is_gone(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 2)
    current = _current_version(pool)
    old = _seed_host(
        pool,
        "host-old",
        image_version="stale",
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    _seed_host(pool, "host-young", image_version=current)
    [alice] = pool.room("alice", 1)
    assert pool.place("alice", alice) == old

    pool.pool("maintain")
    assert pool.host(old).draining_since is not None
    # Its sandbox is still running: a rebuild never interrupts one.
    assert pool.host(old).released_at is None

    _empty(pool, old)
    pool.pool("maintain")

    released = pool.host(old)
    assert released.released_at is not None
    assert released.machine_id in pool.cloud.deleted

    # The pool is one short now, and adds a host made of today's image.
    pool.pool("maintain")
    replacements = [host for host in _hosts(pool) if host.released_at is None]
    assert len(replacements) == 2
    assert {host.image_version for host in replacements} >= {current}


def test_only_one_host_is_rebuilt_at_a_time(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 3)
    for index, name in enumerate(("host-a", "host-b", "host-c")):
        _seed_host(
            pool,
            name,
            image_version="stale",
            created_at=datetime.now(UTC) - timedelta(hours=3 - index),
        )

    pool.pool("maintain")
    assert len(_draining(pool)) == 1

    pool.pool("maintain")
    # The first is gone now; the sweep does not start a second beside it.
    assert len(_draining(pool)) == 0

    pool.pool("maintain")
    assert len(_draining(pool)) == 1


def test_a_busy_host_being_rebuilt_does_not_hold_up_an_empty_one(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 3)
    busy = _seed_host(
        pool,
        "host-busy",
        image_version="stale",
        created_at=datetime.now(UTC) - timedelta(hours=3),
    )
    (alice,) = pool.room("alice", 1)
    assert pool.place("alice", alice) == busy
    _edit(pool, busy, draining_since=datetime.now(UTC))
    empty = _seed_host(
        pool,
        "host-empty",
        image_version="stale",
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    _seed_host(pool, "host-new", image_version=_current_version(pool))

    pool.pool("maintain")

    # The busy one keeps waiting for its sandbox; the empty one goes now.
    assert pool.host(busy).released_at is None
    assert pool.host(empty).draining_since is not None


def test_the_emptiest_out_of_date_host_is_rebuilt_first(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 3)
    old = _seed_host(
        pool,
        "host-old",
        image_version="stale",
        created_at=datetime.now(UTC) - timedelta(hours=3),
    )
    (alice,) = pool.room("alice", 1)
    assert pool.place("alice", alice) == old
    younger = _seed_host(
        pool,
        "host-younger",
        image_version="stale",
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    _seed_host(pool, "host-new", image_version=_current_version(pool))

    pool.pool("maintain")

    assert pool.host(old).draining_since is None
    assert pool.host(younger).draining_since is not None


def test_no_host_is_rebuilt_while_the_others_have_no_room(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 2)
    old = _seed_host(
        pool,
        "host-old",
        image_version="stale",
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    _seed_host(pool, "host-young", image_version=_current_version(pool))
    sessions = pool.room("alice", 4)
    for session in sessions:
        pool.place("alice", session)
    # Both hosts are full: the other cannot take a whole host's sandboxes.
    assert pool.host(old).released_at is None

    pool.pool("maintain")

    assert pool.host(old).draining_since is None


def test_an_old_host_is_rebuilt_even_on_the_current_image(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 2)
    monkeypatch.setattr(settings, "cloud_host_rebuild_after_days", 14)
    current = _current_version(pool)
    old = _seed_host(
        pool,
        "host-old",
        image_version=current,
        created_at=datetime.now(UTC) - timedelta(days=15),
    )
    _seed_host(pool, "host-young", image_version=current)

    pool.pool("maintain")

    assert pool.host(old).draining_since is not None


def test_the_age_rule_can_be_turned_off(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 2)
    monkeypatch.setattr(settings, "cloud_host_rebuild_after_days", 0)
    current = _current_version(pool)
    old = _seed_host(
        pool,
        "host-old",
        image_version=current,
        created_at=datetime.now(UTC) - timedelta(days=365),
    )
    _seed_host(pool, "host-young", image_version=current)

    pool.pool("maintain")

    assert pool.host(old).draining_since is None


def test_a_young_host_of_the_current_image_is_not_rebuilt(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 2)
    current = _current_version(pool)
    _seed_host(pool, "host-a", image_version=current)
    _seed_host(pool, "host-b", image_version=current)

    pool.pool("maintain")

    assert _draining(pool) == []
    assert pool.cloud.created == []


def test_a_pool_sized_by_demand_does_not_rebuild(pool):
    # The fixture runs with cloud_pool_fixed_hosts = 0.
    old = _seed_host(pool, "host-old", image_version="stale")
    _seed_host(pool, "host-young", image_version=_current_version(pool))

    pool.pool("maintain")

    assert pool.host(old).draining_since is None


def test_the_admin_page_shows_the_size_and_which_hosts_are_out_of_date(
    pool, monkeypatch
):
    monkeypatch.setattr(settings, "platform_admin_handles", ["ops"])
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 2)
    current = _current_version(pool)
    monkeypatch.setattr(
        enrollment, "current_host_image_version", AsyncMock(return_value=current)
    )
    _seed_host(
        pool,
        "host-old",
        image_version="stale",
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    _seed_host(pool, "host-young", image_version=current)

    pool.pool("maintain")
    data = pool.client.get(
        "/admin/stats/machines", headers=session_auth_headers("ops")
    ).json()["data"]

    hosts = {host["hostname"]: host for host in data["hosts"]}
    assert data["pool"]["fixed_hosts"] == 2
    assert data["config"]["rebuild_after_days"] == 14
    assert hosts["host-old"]["image_version"] == "stale"
    assert hosts["host-old"]["image_outdated"] is True
    assert hosts["host-old"]["draining"] is True
    assert hosts["host-young"]["image_outdated"] is False
    assert hosts["host-young"]["draining"] is False
