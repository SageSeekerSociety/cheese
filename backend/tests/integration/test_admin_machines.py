"""The admin page's machines: the platform's cloud hosts, the sandboxes on each,
and how long sessions waited for one, for the platform's admins only."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.domain.machine.models import CloudHost, MachineStatus, SandboxHome
from app.domain.run_record.models import RunRecord
from tests.integration.conftest import post_project, session_auth_headers


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(settings, "platform_admin_handles", ["ops"])
    return session_auth_headers("ops")


def _room(client) -> tuple[uuid.UUID, uuid.UUID]:
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    return uuid.UUID(project["id"]), uuid.UUID(room)


def _host(name: str, **fields) -> CloudHost:
    return CloudHost(
        **{
            "machine_id": abs(hash(name)) % 10_000,
            "customer_id": 1,
            "account_id": 1,
            "offering_id": 1,
            "hostname": name,
            "login_user": "cheese",
            "cores": 4,
            "memory_mb": 16384,
            "disk_gb": 40,
            "status": MachineStatus.running,
            **fields,
        }
    )


def _home(host, project, room, **fields) -> SandboxHome:
    return SandboxHome(
        host_id=host.id,
        spec="standard",
        project_id=project,
        topic_id=room,
        room_resource_id=str(room),
        resource_id=str(uuid.uuid4()),
        session_id=uuid.uuid4(),
        **fields,
    )


def _seed(client, build) -> None:
    async def add() -> None:
        async with client.test_factory() as s:
            await build(s)
            await s.commit()

    client.portal.call(add)


def test_only_a_platform_admin_reads_it(client, admin):
    assert client.get("/admin/stats/machines", headers=admin).status_code == 200
    stranger = session_auth_headers("someone")
    assert client.get("/admin/stats/machines", headers=stranger).status_code == 403


def test_each_host_shows_its_sandboxes_and_the_pool_counts_who_waits(client, admin):
    project, room = _room(client)
    now = datetime.now(UTC)

    async def build(s):
        busy = _host("busy", enrolled_at=now - timedelta(hours=1), device_id="d-busy")
        fresh = _host("fresh", machine_id=None)
        s.add_all([busy, fresh])
        await s.flush()
        s.add_all(
            [
                _home(busy, project, room),
                _home(busy, project, room, waiting_since=now - timedelta(minutes=3)),
            ]
        )

    _seed(client, build)

    data = client.get("/admin/stats/machines", headers=admin).json()["data"]
    hosts = {h["hostname"]: h for h in data["hosts"]}
    assert hosts["busy"]["used"] == 2
    assert {s["room"] for s in hosts["busy"]["sandboxes"]} == {"Room"}
    assert sorted(s["state"] for s in hosts["busy"]["sandboxes"]) == [
        "waiting",
        "working",
    ]
    # Enrolled, but its connector is not linked to this backend.
    assert hosts["busy"]["phase"] == "offline"
    # Asked of the provider, no machine yet.
    assert hosts["fresh"]["phase"] == "creating"
    assert data["pool"]["hosts"] == 2
    assert data["pool"]["used"] == 2
    assert data["pool"]["waiting"] == 1


def test_the_pool_says_it_refuses_new_sessions_while_the_provider_keeps_failing(
    client, admin
):
    now = datetime.now(UTC)

    async def build(s):
        s.add_all(
            [
                _host(f"bad-{i}", failed_at=now - timedelta(minutes=i), released_at=now)
                for i in range(3)
            ]
        )

    _seed(client, build)

    pool = client.get("/admin/stats/machines", headers=admin).json()["data"]["pool"]
    assert pool["provider_failures"] == 3
    assert pool["refusing"] is True
    # Given up on: not counted as hosts of the pool.
    assert pool["hosts"] == 0


def test_a_few_failures_do_not_make_the_pool_refuse(client, admin):
    now = datetime.now(UTC)

    async def build(s):
        s.add(_host("bad", failed_at=now, released_at=now))

    _seed(client, build)

    pool = client.get("/admin/stats/machines", headers=admin).json()["data"]["pool"]
    assert (pool["provider_failures"], pool["refusing"]) == (1, False)


def test_the_wait_for_a_sandbox_is_from_preparing_to_ready(client, admin):
    project, room = _room(client)
    now = datetime.now(UTC)
    quick, slow, abandoned = (str(uuid.uuid4()) for _ in range(3))

    def startup(home, ago):
        return RunRecord(
            project_id=project,
            conversation_id=room,
            kind="cloud_startup",
            content="正在准备沙箱",
            meta={"home": home, "i18n": {"content": {"key": "sandboxPreparing"}}},
            created_at=now - ago,
        )

    def ready(home, ago):
        return RunRecord(
            project_id=project,
            conversation_id=room,
            kind="cloud_provisioning",
            content="沙箱已就绪",
            meta={"home": home, "state": "ready"},
            created_at=now - ago,
        )

    async def build(s):
        s.add_all(
            [
                startup(quick, timedelta(minutes=10)),
                ready(quick, timedelta(minutes=10) - timedelta(seconds=30)),
                startup(slow, timedelta(minutes=20)),
                ready(slow, timedelta(minutes=15)),
                startup(abandoned, timedelta(minutes=30)),
                # Before the day the page looks at.
                startup(str(uuid.uuid4()), timedelta(days=2)),
            ]
        )

    _seed(client, build)

    history = client.get("/admin/stats/machines", headers=admin).json()["data"][
        "history"
    ]
    assert (history["prepared"], history["ready"], history["abandoned"]) == (3, 2, 1)
    assert history["wait_max_s"] == pytest.approx(300, abs=1)
    assert 30 <= history["wait_p50_s"] <= 300
