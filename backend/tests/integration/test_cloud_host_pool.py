"""The platform's cloud host pool: where sessions' sandboxes land, and when
hosts come and go.

A host belongs to the platform. Sessions from any project are placed on any
host with a free slot; a warm machine is taken only when none has room, and a
host is created only when the warm pool is empty too. A host that carries no
session home is released after the idle hold, never while a session left work
on it that it did not push.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.agent.device_hub import device_hub
from app.domain.agent_session.models import AgentSession
from app.domain.block.models import Block, BlockKind
from app.domain.device.models import DeviceProjectRow, DeviceRow, DeviceTeamRow
from app.domain.device.supply import Supply
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.machine import owner_reads, services
from app.domain.machine.models import (
    HOST_OWNER,
    AiStatus,
    CloudHost,
    CloudHostHome,
    MachineStatus,
    WarmMachine,
)
from app.domain.machine.services import (
    CUSTOMER_REF,
    CloudKeepsFailing,
    CloudPoolFull,
    HostPool,
)
from app.domain.run_record.models import RunRecord
from app.domain.user.repositories import UserRepository
from tests.integration.conftest import post_project, session_auth_headers
from tests.microcloud import FakeMicroCloud


class Case:
    def __init__(self, client, cloud, online):
        self.client = client
        self.cloud = cloud
        self.online = online
        self.projects: dict[str, dict] = {}

    def run(self, coroutine_fn):
        return self.client.portal.call(coroutine_fn)

    def room(self, owner: str, sessions: int) -> list[uuid.UUID]:
        """A project of ``owner``'s with one room, and that many agent sessions
        in it; returns the sessions."""
        project = post_project(
            self.client, json={"name": f"{owner} project"}, owner=owner
        ).json()["data"]
        room = self.client.post(
            "/topics",
            json={"project_id": project["id"], "title": "Room"},
            headers=session_auth_headers(owner),
        ).json()["data"]["id"]

        async def seed():
            async with self.client.test_request_factory() as db:
                user = await UserRepository(db).get_by_handle(owner)
                rows = [
                    AgentSession(
                        conversation_id=uuid.UUID(room),
                        agent_handle=f"agent-{n}",
                        harness="claude-code",
                    )
                    for n in range(sessions)
                ]
                db.add_all(rows)
                await db.commit()
                self.projects[owner] = {
                    "id": uuid.UUID(project["id"]),
                    "room": uuid.UUID(room),
                    "actor": Actor(owner, user.id, "token"),
                }
                return [row.id for row in rows]

        return self.run(seed)

    def place(self, owner: str, session_id: uuid.UUID) -> uuid.UUID:
        async def go():
            async with self.client.test_request_factory() as db:
                host = await HostPool(db, self.cloud).place(
                    session_id,
                    actor=self.projects[owner]["actor"],
                    resource_id=str(uuid.uuid4()),
                )
                await db.commit()
                return host.id

        return self.run(go)

    def up(self, host_id: uuid.UUID, device_id: str | None = None) -> str:
        """What the pool sweep finds once the host is running and its connector
        has dialled in."""
        device_id = device_id or f"dev-{host_id.hex[:8]}"

        async def go():
            async with self.client.test_request_factory() as db:
                host = await db.get(CloudHost, host_id)
                self.cloud.machines[host.machine_id].update(
                    status="running", aiStatus="disabled"
                )
                host.status = MachineStatus.running
                host.ai_status = AiStatus.disabled
                host.device_id = device_id
                host.enrolled_at = datetime.now(UTC)
                host.last_seen_at = datetime.now(UTC)
                await db.commit()

        self.run(go)
        self.online.add(device_id)
        return device_id

    def works_on(self, session_id: uuid.UUID, device_id: str) -> None:
        """The session's tool calls run in its sandbox on that host."""

        async def go():
            async with self.client.test_request_factory() as db:
                row = await db.get(AgentSession, session_id)
                row.work_lease = {
                    "kind": "device",
                    "session_id": str(session_id),
                    "device_id": device_id,
                    "status": "ready",
                }
                await db.commit()

        self.run(go)

    def host(self, host_id: uuid.UUID) -> CloudHost:
        async def go():
            async with self.client.test_request_factory() as db:
                return await db.get(CloudHost, host_id)

        return self.run(go)

    def pool(self, method: str, *args, **kwargs):
        async def go():
            async with self.client.test_request_factory() as db:
                result = await getattr(HostPool(db, self.cloud), method)(
                    *args, **kwargs
                )
                await db.commit()
                return result

        return self.run(go)

    def room_lines(self, owner: str) -> list[str]:
        async def go():
            async with self.client.test_request_factory() as db:
                rows = await db.scalars(
                    select(RunRecord.content)
                    .where(
                        RunRecord.conversation_id == self.projects[owner]["room"],
                        RunRecord.kind.in_(["cloud_startup", "cloud_provisioning"]),
                    )
                    .order_by(RunRecord.created_at)
                )
                return list(rows)

        return self.run(go)

    def said(self, owner: str) -> list[str]:
        """What the platform said in the room's conversation about its
        sandboxes: what a person there has to know."""

        async def go():
            async with self.client.test_request_factory() as db:
                rows = await db.scalars(
                    select(Block.content)
                    .where(
                        Block.conversation_id == self.projects[owner]["room"],
                        Block.kind == BlockKind.event,
                        Block.meta["event_type"].as_string() == "cloud_startup",
                    )
                    .order_by(Block.created_at)
                )
                return list(rows)

        return self.run(go)


@pytest.fixture
def pool(client, monkeypatch):
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    # Two slots a host: two cores, one sandbox each, and memory for both.
    monkeypatch.setattr(settings, "microcloud_default_cores", 2)
    monkeypatch.setattr(settings, "cloud_host_slots_per_core", 1)
    monkeypatch.setattr(settings, "microcloud_default_memory_mb", 4096)
    monkeypatch.setattr(settings, "cloud_sandbox_memory_mb", 1536)
    monkeypatch.setattr(settings, "cloud_pool_min_free_slots", 0)
    monkeypatch.setattr(settings, "cloud_host_idle_hold_s", 600)
    monkeypatch.setattr(settings, "cloud_pool_max_hosts", 20)
    online: set[str] = set()
    monkeypatch.setattr(device_hub, "is_online", lambda device: device in online)
    return Case(client, FakeMicroCloud(), online)


def _later(monkeypatch, by: timedelta) -> None:
    """The pool's clock reads ``by`` later than now from here on."""

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + by

    monkeypatch.setattr(services, "datetime", Later)


def _two_hosts_at_work(pool) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, str, str]:
    """Alice's session works on one host, which her second session fills, and
    Bob's on another. Returns Alice's working session, her host, Bob's host
    and the two hosts' devices."""
    alice = pool.room("alice", 2)
    [bob] = pool.room("bob", 1)
    hers = pool.place("alice", alice[0])
    her_device = pool.up(hers)
    assert pool.place("alice", alice[1]) == hers
    his = pool.place("bob", bob)
    assert his != hers
    his_device = pool.up(his)
    pool.works_on(alice[0], her_device)
    pool.works_on(bob, his_device)
    return alice[0], hers, his, her_device, his_device


LOST_LINE = (
    "环境所在的机器不再响应，环境已换成新的："
    "新环境从仓库里已推送的内容开始，没推送的改动不在了"
)


def _warm_machine_ready(case: Case) -> str:
    """One warm machine prepared ahead of demand, with its connector up."""

    async def seed():
        async with case.client.test_request_factory() as db:
            owner = await IdentityService(db).ensure_agent_user(
                handle="cheese-warm-pool"
            )
            db.add(
                DeviceRow(
                    device_id="warm-1",
                    name="warm-1",
                    token="warm-token",
                    owner_user_id=owner.id,
                    supply=Supply.cloud,
                    created_at=datetime.now(UTC),
                )
            )
            db.add(
                WarmMachine(
                    state="ready",
                    machine_id=500,
                    device_id="warm-1",
                    enrolled_at=datetime.now(UTC),
                    ip="192.0.2.50",
                    create_request={
                        "hostname": "warm-1",
                        "offeringId": 1,
                        "cores": 2,
                        "memoryMb": settings.microcloud_default_memory_mb,
                        "diskGb": settings.microcloud_default_disk_gb,
                        "user": settings.microcloud_login_user,
                        "aiMode": "none",
                    },
                )
            )
            await db.commit()

    case.run(seed)
    case.online.add("warm-1")
    return "warm-1"


def test_sessions_of_two_projects_share_a_host_with_room(pool):
    [alice] = pool.room("alice", 1)
    [bob] = pool.room("bob", 1)

    first = pool.place("alice", alice)
    pool.up(first)
    second = pool.place("bob", bob)

    assert second == first
    assert len(pool.cloud.created) == 1
    # Created under the platform's own MicroCloud customer, not a project's.
    assert list(pool.cloud.customers) == [CUSTOMER_REF]
    assert pool.cloud.created[0]["hostname"].startswith("host-")
    # Placing again answers the same host and asks the provider for nothing.
    assert pool.place("alice", alice) == first
    assert len(pool.cloud.created) == 1


def _enrolled_long_ago_and_offline(pool, host_id, device, *, offline_for=None):
    async def go():
        async with pool.client.test_request_factory() as db:
            host = await db.get(CloudHost, host_id)
            host.enrolled_at = datetime.now(UTC) - timedelta(hours=1)
            if offline_for is not None:
                host.offline_since = datetime.now(UTC) - offline_for
            await db.commit()

    pool.run(go)
    pool.online.discard(device)


def test_an_idle_host_not_yet_dialled_back_after_a_restart_is_kept(pool):
    """Right after the backend restarts, no host's connector has dialled back
    yet. An idle host the sweep finds offline then is not one that never
    connected, and is kept."""
    [alice] = pool.room("alice", 1)
    first = pool.place("alice", alice)
    device = pool.up(first)
    _enrolled_long_ago_and_offline(pool, first, device)

    pool.pool("maintain")

    assert pool.host(first).released_at is None


def test_an_idle_host_whose_connector_stays_away_is_replaced(pool):
    """A host whose connector has stayed away past the time one takes to dial
    in, with nobody working on it, holds nothing anyone needs: it goes."""
    [alice] = pool.room("alice", 1)
    first = pool.place("alice", alice)
    device = pool.up(first)
    _enrolled_long_ago_and_offline(
        pool, first, device, offline_for=timedelta(minutes=6)
    )

    pool.pool("maintain")

    assert pool.host(first).released_at is not None


def test_a_host_whose_connector_went_away_takes_no_new_session(pool):
    """A host whose connector was up and has been gone past the time a
    connector takes to dial in cannot run a sandbox now, and may not for
    hours. A new session is not put there to wait on it: it goes where a
    sandbox can start."""
    [alice] = pool.room("alice", 1)
    [bob] = pool.room("bob", 1)
    first = pool.place("alice", alice)
    device = pool.up(first)

    async def gone_since_long_ago():
        async with pool.client.test_request_factory() as db:
            host = await db.get(CloudHost, first)
            host.enrolled_at = datetime.now(UTC) - timedelta(hours=1)
            host.last_seen_at = datetime.now(UTC) - timedelta(minutes=50)
            await db.commit()

    pool.run(gone_since_long_ago)
    pool.online.discard(device)

    assert pool.place("bob", bob) != first


def test_a_host_takes_no_more_sandboxes_than_its_memory_holds(pool, monkeypatch):
    """Cores leave room for two sandboxes, but a 4 GiB host keeps 1 GiB for
    itself and has 3 GiB for its sandboxes: one at a 3 GiB limit. The second
    session's sandbox goes to another host."""
    monkeypatch.setattr(settings, "cloud_sandbox_memory_mb", 3072)
    [alice] = pool.room("alice", 1)
    [bob] = pool.room("bob", 1)
    first = pool.place("alice", alice)
    pool.up(first)

    assert pool.place("bob", bob) != first


def test_sessions_placed_at_once_share_the_one_host_being_created(pool):
    [alice] = pool.room("alice", 1)
    [bob] = pool.room("bob", 1)

    async def both():
        async def one(owner, session_id):
            async with pool.client.test_request_factory() as db:
                host = await HostPool(db, pool.cloud).place(
                    session_id,
                    actor=pool.projects[owner]["actor"],
                    resource_id=str(uuid.uuid4()),
                )
                await db.commit()
                return host.id

        return await asyncio.gather(one("alice", alice), one("bob", bob))

    a, b = pool.run(both)

    assert a == b
    assert len(pool.cloud.created) == 1


def test_a_full_host_takes_a_warm_machine_before_creating_one(pool):
    first_two = pool.room("alice", 2)
    [bob] = pool.room("bob", 1)
    full = pool.place("alice", first_two[0])
    pool.up(full)
    assert pool.place("alice", first_two[1]) == full
    warm = _warm_machine_ready(pool)

    claimed = pool.place("bob", bob)

    assert claimed != full
    assert len(pool.cloud.created) == 1
    [(machine_id, claim)] = pool.cloud.claims
    assert machine_id == 500
    # Claimed for the pool under the platform's account, keyed by the host.
    assert claim["claimKey"] == str(claimed)
    assert claim["customerId"] == pool.cloud.customers[CUSTOMER_REF]["id"]
    host = pool.host(claimed)
    assert host.device_id == warm and not host.warm_claim_pending

    async def device():
        async with pool.client.test_request_factory() as db:
            row = await db.get(DeviceRow, warm)
            owner = await UserRepository(db).get_by_handle(HOST_OWNER)
            teams = (
                await db.scalars(
                    select(DeviceTeamRow).where(DeviceTeamRow.device_id == warm)
                )
            ).all()
            projects = (
                await db.scalars(
                    select(DeviceProjectRow).where(DeviceProjectRow.device_id == warm)
                )
            ).all()
            return row.owner_user_id == owner.id, teams, projects

    owned_by_pool, teams, projects = pool.run(device)
    # The device is the platform's: no team or project lists it as theirs.
    assert owned_by_pool and teams == [] and projects == []


def test_with_the_warm_pool_empty_a_new_host_is_created(pool):
    first_two = pool.room("alice", 2)
    [bob] = pool.room("bob", 1)
    full = pool.place("alice", first_two[0])
    pool.up(full)
    pool.place("alice", first_two[1])

    cold = pool.place("bob", bob)

    assert cold != full
    assert len(pool.cloud.created) == 2
    assert pool.cloud.claims == []
    assert pool.host(cold).device_id is None  # bob waits for it to come up


def test_an_idle_host_is_released_after_the_hold_and_not_before(pool):
    [alice] = pool.room("alice", 1)
    host_id = pool.place("alice", alice)
    pool.up(host_id)
    pool.pool("leave", alice, kept_work=False)

    pool.pool("maintain")
    host = pool.host(host_id)
    assert host.idle_since is not None and host.released_at is None
    assert pool.cloud.deleted == []

    async def long_ago():
        async with pool.client.test_request_factory() as db:
            row = await db.get(CloudHost, host_id)
            row.idle_since = datetime.now(UTC) - timedelta(
                seconds=settings.cloud_host_idle_hold_s + 1
            )
            await db.commit()

    pool.run(long_ago)
    pool.pool("maintain")

    assert pool.host(host_id).released_at is not None
    assert pool.cloud.deleted == [pool.host(host_id).machine_id]


def test_a_host_is_not_released_while_a_session_left_unpushed_work_on_it(pool):
    [alice] = pool.room("alice", 1)
    host_id = pool.place("alice", alice)
    device = pool.up(host_id)
    pool.pool("leave", alice, kept_work=True)

    async def long_ago():
        async with pool.client.test_request_factory() as db:
            row = await db.get(CloudHost, host_id)
            row.idle_since = datetime.now(UTC) - timedelta(days=1)
            await db.commit()
            home = await db.scalar(
                select(CloudHostHome).where(CloudHostHome.session_id == alice)
            )
            return home.resource_id

    resource = pool.run(long_ago)
    pool.pool("maintain")

    host = pool.host(host_id)
    assert host.released_at is None and host.idle_since is None
    assert pool.cloud.deleted == []

    # The room's cleanup removed the directory: nothing is only there any more.
    pool.pool("forget_device_homes", device, resource)
    pool.pool("maintain")
    assert pool.host(host_id).idle_since is not None


def test_the_pool_adds_a_host_before_its_free_slots_run_out(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_min_free_slots", 2)
    [alice] = pool.room("alice", 1)
    host_id = pool.place("alice", alice)
    pool.up(host_id)
    # One slot of two is free: below the floor of two.

    pool.pool("maintain")

    assert len(pool.cloud.created) == 2

    async def hosts():
        async with pool.client.test_request_factory() as db:
            return list(await db.scalars(select(CloudHost.id)))

    spare = next(h for h in pool.run(hosts) if h != host_id)
    pool.up(spare)

    async def long_ago():
        async with pool.client.test_request_factory() as db:
            row = await db.get(CloudHost, spare)
            row.idle_since = datetime.now(UTC) - timedelta(days=1)
            await db.commit()

    pool.run(long_ago)
    pool.pool("maintain")
    # Idle past the hold, but releasing it would leave fewer free slots than
    # the floor: it stays, and no third host is asked for.
    assert pool.host(spare).released_at is None
    assert len(pool.cloud.created) == 2


def test_a_full_pool_tells_the_session_to_try_later(pool, monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_max_hosts", 1)
    sessions = pool.room("alice", 3)
    host_id = pool.place("alice", sessions[0])
    pool.up(host_id)
    pool.place("alice", sessions[1])

    with pytest.raises(CloudPoolFull) as refused:
        pool.place("alice", sessions[2])

    assert "资源紧张" in str(refused.value)
    assert len(pool.cloud.created) == 1


def test_a_host_the_provider_failed_is_let_go_and_its_session_placed_again(pool):
    [alice] = pool.room("alice", 1)
    failed = pool.place("alice", alice)
    pool.pool("tell_waiting", alice)
    pool.cloud.machines[pool.host(failed).machine_id]["status"] = "error"
    pool.pool("refresh_due")

    again = pool.place("alice", alice)

    assert again != failed
    host = pool.host(failed)
    assert host.released_at is not None and host.failed_at is not None
    assert pool.cloud.deleted == [host.machine_id]
    assert len(pool.cloud.created) == 2
    lines = pool.room_lines("alice")
    assert lines == ["正在准备环境", "环境准备失败，正在重新准备"]


def test_a_provider_that_keeps_failing_stops_the_pool_creating_hosts(pool):
    [alice] = pool.room("alice", 1)
    for _ in range(3):
        host_id = pool.place("alice", alice)
        pool.cloud.machines[pool.host(host_id).machine_id]["status"] = "error"
        pool.pool("refresh_due")
        pool.pool("maintain")

    with pytest.raises(CloudKeepsFailing):
        pool.place("alice", alice)
    assert len(pool.cloud.created) == 3


def _fail_hosts(pool, owner, session, count):
    """``count`` hosts in a row that the provider builds into ``error``, each
    then deleted at the provider and forgotten by it, as MicroCloud does."""
    for _ in range(count):
        host_id = pool.place(owner, session)
        pool.cloud.machines[pool.host(host_id).machine_id]["status"] = "error"
        pool.pool("refresh_due")
        pool.pool("maintain")
        pool.pool("refresh_due")


def test_failures_still_count_after_the_provider_forgot_the_machines(pool, monkeypatch):
    monkeypatch.setattr(settings, "microcloud_reconcile_interval_s", 0)
    [alice] = pool.room("alice", 1)
    _fail_hosts(pool, "alice", alice, 3)

    with pytest.raises(CloudKeepsFailing):
        pool.place("alice", alice)
    assert len(pool.cloud.created) == 3


def test_a_failing_provider_is_tried_again_after_the_probe_interval(pool, monkeypatch):
    monkeypatch.setattr(settings, "microcloud_reconcile_interval_s", 0)
    [alice] = pool.room("alice", 1)
    _fail_hosts(pool, "alice", alice, 3)

    async def six_minutes_ago():
        async with pool.client.test_request_factory() as db:
            for host in await db.scalars(
                select(CloudHost).where(CloudHost.failed_at.is_not(None))
            ):
                host.failed_at -= timedelta(minutes=6)
            await db.commit()

    pool.run(six_minutes_ago)

    probe = pool.place("alice", alice)

    assert len(pool.cloud.created) == 4
    assert pool.host(probe).failed_at is None


def test_the_failure_that_stops_the_pool_is_reported_as_an_error(
    pool, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "microcloud_reconcile_interval_s", 0)
    [alice] = pool.room("alice", 1)

    with caplog.at_level(logging.WARNING, logger="cheese.machine"):
        _fail_hosts(pool, "alice", alice, 2)
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        _fail_hosts(pool, "alice", alice, 1)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "failed 3 hosts" in errors[0].getMessage()


def test_the_room_hears_about_the_sandbox_not_the_host(pool):
    [alice] = pool.room("alice", 1)
    host_id = pool.place("alice", alice)
    pool.pool("tell_waiting", alice)
    pool.pool("tell_waiting", alice)  # said once
    pool.up(host_id)

    pool.pool("maintain")

    lines = pool.room_lines("alice")
    assert lines == ["正在准备环境", "环境已就绪"]
    hostname = pool.host(host_id).hostname
    assert not any(hostname in line for line in lines)


def test_the_device_owner_admits_tool_calls_only_on_live_hosts(pool):
    [alice] = pool.room("alice", 1)
    host_id = pool.place("alice", alice)

    async def device_row():
        async with pool.client.test_request_factory() as db:
            owner = await IdentityService(db).ensure_agent_user(handle=HOST_OWNER)
            db.add(
                DeviceRow(
                    device_id="host-device",
                    name="host",
                    token="host-token",
                    owner_user_id=owner.id,
                    supply=Supply.cloud,
                    created_at=datetime.now(UTC),
                )
            )
            await db.commit()

    pool.run(device_row)
    pool.up(host_id, "host-device")

    async def admitted():
        async with pool.client.test_request_factory() as db:
            return await owner_reads.active_cloud_host(db, "host-device")

    assert pool.run(admitted)

    async def release():
        async with pool.client.test_request_factory() as db:
            row = await db.get(CloudHost, host_id)
            row.released_at = datetime.now(UTC)
            await db.commit()

    pool.run(release)
    assert not pool.run(admitted)


def test_a_lost_claim_answer_is_retried_as_the_same_claim(pool):
    [alice] = pool.room("alice", 1)
    _warm_machine_ready(pool)
    pool.cloud.fail_claim = True

    first = pool.place("alice", alice)
    assert pool.host(first).warm_claim_pending
    pool.cloud.fail_claim = False
    again = pool.place("alice", alice)

    assert again == first
    assert pool.host(first).device_id == "warm-1"
    assert len(pool.cloud.claims) == 2
    assert pool.cloud.claims[0] == pool.cloud.claims[1]
    assert pool.cloud.created == []


def test_asking_again_while_the_claim_is_out_waits_for_it(pool):
    [alice] = pool.room("alice", 1)
    _warm_machine_ready(pool)
    in_flight = asyncio.Event()
    release = asyncio.Event()
    claim = pool.cloud.claim_warm_machine

    async def slow_claim(machine_id, body):
        in_flight.set()
        await release.wait()
        return await claim(machine_id, body)

    pool.cloud.claim_warm_machine = slow_claim

    async def place():
        async with pool.client.test_request_factory() as db:
            host = await HostPool(db, pool.cloud).place(
                alice,
                actor=pool.projects["alice"]["actor"],
                resource_id=str(uuid.uuid4()),
            )
            await db.commit()
            return host.id, host.device_id

    async def run():
        first = asyncio.create_task(place())
        await asyncio.wait_for(in_flight.wait(), timeout=5)
        second = asyncio.create_task(place())
        await asyncio.sleep(0.3)  # the second is waiting for the claim
        release.set()
        return await asyncio.wait_for(asyncio.gather(first, second), timeout=10)

    a, b = pool.run(run)

    assert a == b and a[1] == "warm-1"
    assert len(pool.cloud.claims) == 1


def test_a_warm_machine_that_cannot_be_claimed_is_given_up(pool):
    [alice] = pool.room("alice", 1)
    _warm_machine_ready(pool)
    pool.cloud.fail_claim = True
    stuck = pool.place("alice", alice)
    hosts = {stuck}
    for _ in range(4):
        hosts.add(pool.place("alice", alice))

    # The fifth refused claim gives the host up; the session is placed on a
    # host created for it instead.
    [fresh] = hosts - {stuck}
    assert pool.host(stuck).released_at is not None
    assert pool.host(fresh).released_at is None
    assert len(pool.cloud.claims) == 5
    assert len(pool.cloud.created) == 1


def test_an_enrolled_host_in_error_is_given_up_and_its_session_placed_again(pool):
    """A sandbox is disposable: a host the provider reports broken is not
    waited on, even with the session's work on it."""
    [alice] = pool.room("alice", 1)
    host_id = pool.place("alice", alice)
    pool.works_on(alice, pool.up(host_id))
    pool.cloud.machines[pool.host(host_id).machine_id]["status"] = "error"

    async def stale():
        async with pool.client.test_request_factory() as db:
            row = await db.get(CloudHost, host_id)
            row.last_seen_at = None
            await db.commit()

    pool.run(stale)
    pool.pool("refresh_due")
    pool.pool("maintain")

    host = pool.host(host_id)
    assert host.released_at is not None
    # The provider failed it, so it counts against the provider's record.
    assert host.failed_at is not None
    assert pool.cloud.deleted == [host.machine_id]
    assert pool.place("alice", alice) != host_id
    assert pool.said("alice") == [LOST_LINE]


def test_a_session_whose_host_stopped_answering_gets_a_new_sandbox(pool, monkeypatch):
    """On 2026-10-05 sessions waited hours on a host whose connector hung. A
    host gone ten minutes, while another host answers, is given up."""
    alice, lost, other, device, _ = _two_hosts_at_work(pool)
    pool.online.discard(device)

    pool.pool("maintain")
    # Away for a moment is not lost: links drop and come back.
    assert pool.host(lost).released_at is None

    _later(monkeypatch, timedelta(minutes=11))
    pool.pool("maintain")

    host = pool.host(lost)
    assert host.released_at is not None
    # Not counted as the provider failing: nothing says it did.
    assert host.failed_at is None
    assert pool.cloud.deleted == [host.machine_id]
    assert pool.host(other).released_at is None
    assert pool.place("alice", alice) not in {lost, None}
    assert pool.said("alice") == [LOST_LINE]
    assert pool.said("bob") == []


def test_with_every_host_away_nothing_is_given_up_until_one_answers(pool, monkeypatch):
    """Every connector gone at once is the platform's outage — a restart, a
    cut link — not every host lost: the sandboxes are kept for when it ends."""
    alice, lost, other, device, his_device = _two_hosts_at_work(pool)
    pool.online.discard(device)
    pool.online.discard(his_device)
    pool.pool("maintain")
    _later(monkeypatch, timedelta(hours=1))

    pool.pool("maintain")

    assert pool.host(lost).released_at is None
    assert pool.host(other).released_at is None
    assert pool.cloud.deleted == []

    # Bob's host is back and Alice's is not: hers is lost.
    pool.online.add(his_device)
    pool.pool("maintain")

    assert pool.host(lost).released_at is not None
    assert pool.host(other).released_at is None
