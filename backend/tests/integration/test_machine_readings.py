"""What the platform's hosts and their sandboxes use, for the admin machines
page, and a host running short told to a person.

The rules, as a person would state them before any of this was built:

- the machines page shows, for each host, how much of its disk, inodes,
  memory and CPU it used at its last reading and when that was, and for each
  sandbox the memory it holds and the most it has held, and what its home
  holds on disk;
- a host with no sandbox on it is still read;
- a host past its line of disk, inodes or memory on two readings running is
  reported to the alert group, each host on its own; a single reading past it
  — a spike gone a minute later — is not, and an ongoing one is not repeated
  every minute.

The hosts and what they run are those of ``test_sandbox_disk_guard``; what a
host's kernel would say of its memory and its sandboxes' cgroups is given,
since this machine has neither.
"""

import asyncio
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core import alerting
from app.core.config import settings
from app.domain.machine import readings
from app.domain.machine.models import HostReading, SandboxHome, SandboxUsage
from tests.integration.conftest import session_auth_headers
from tests.integration.test_cloud_host_pool import pool  # noqa: F401 — fixture
from tests.integration.test_sandbox_disk_guard import (
    ROOM,
    files,
    full_since,
    guard,
)
from tests.integration.test_sandbox_idle_stop import (  # noqa: F401 — fixture
    cloud,
    home_of,
    host_of,
    run,
    sandbox_dir,
    working_on,
)

GIB = 2**30


def kernel_says(
    case,
    machines: dict[str, dict],
    sandboxes: dict[str, list],
    filesystem: dict = ROOM,
    homes: dict[str, list] | None = None,
) -> None:
    """From now on each host's reading reports this of its memory and CPU,
    these sandbox cgroups, this of its disk, and this of each home by its
    quota."""
    real = case.hosts.exec

    async def exec_(device_id, argv, *, stdin=None, **kwargs):
        result = await real(device_id, argv, stdin=stdin, **kwargs)
        if stdin and '"action": "disk"' in stdin and not result["exit"]:
            answer = json.loads(result["stdout"].strip().splitlines()[-1])
            answer["filesystem"] = filesystem
            answer["machine"] = machines.get(device_id, {})
            answer["sandboxes"] = sandboxes.get(device_id, [])
            answer["homes"] = (homes or {}).get(device_id, [])
            result = {**result, "stdout": json.dumps(answer)}
        return result

    case.hosts.exec = exec_


def memory(
    used_gib: float,
    *,
    total_gib: int = 16,
    cpu_usec: int = 0,
    swap_gib: int = 8,
    swap_used_gib: float = 1,
    pressure: bool = True,
) -> dict:
    return {
        "memory": total_gib * GIB,
        "memory_available": int((total_gib - used_gib) * GIB),
        "swap_bytes": swap_gib * GIB,
        "swap_free_bytes": int((swap_gib - swap_used_gib) * GIB),
        "memory_pressure": (
            {
                "some": {"avg10": 12.5, "avg60": 3.0},
                "full": {"avg10": 4.5, "avg60": 1.0},
            }
            if pressure
            else None
        ),
        "zswap": False,
        "cpu_usage_usec": cpu_usec,
        "load1": 0.5,
    }


def readings_go_back(case, by: timedelta) -> None:
    """Every reading so far was taken ``by`` earlier."""

    async def age():
        async with case.client.test_request_factory() as db:
            await db.execute(
                update(HostReading).values(read_at=HostReading.read_at - by)
            )
            await db.commit()

    run(case, age)


def page(case) -> dict:
    return case.client.get(
        "/admin/stats/machines", headers=session_auth_headers("ops")
    ).json()["data"]


class _Alerts:
    def __init__(self) -> None:
        self.posted: list[str] = []

    async def post(self, text: str) -> None:
        self.posted.append(text)


@pytest.fixture
def alerts(monkeypatch) -> _Alerts:
    box = _Alerts()
    monkeypatch.setattr(settings, "feishu_alert_webhook", "https://alerts.test/hook")
    monkeypatch.setattr(alerting, "_post", box.post)
    monkeypatch.setattr(
        alerting, "REPEAT_KEY_PREFIX", f"cheese:alert:test:{uuid.uuid4().hex}:"
    )
    monkeypatch.setattr(alerting, "_asked", {})
    monkeypatch.setattr(alerting, "budget", alerting._Budget())
    return box


def posted(case, box: _Alerts) -> list[str]:
    """What reached the alert group once the sends under way have finished."""

    async def settle():
        await asyncio.gather(*list(alerting._running))

    run(case, settle)
    return box.posted


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "platform_admin_handles", ["ops"])


def test_the_page_shows_what_each_host_and_its_sandboxes_use(cloud):  # noqa: F811
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-1")
    resource = home_of(cloud, seat).resource_id
    sandbox = [
        {
            "resource": resource,
            "memory": GIB,
            "memory_peak": 2 * GIB,
            "anon": 700 * 2**20,
            "file": 300 * 2**20,
            "swap": 0,
            "oom_kill": 0,
        }
    ]
    quota = [
        {
            "project": str(cloud.project_id),
            "resource": resource,
            "bytes": 3 * GIB,
            "inodes": 40,
        }
    ]
    kernel_says(
        cloud,
        {"host-1": memory(4, cpu_usec=0)},
        {"host-1": sandbox},
        homes={"host-1": quota},
    )
    guard(cloud)
    readings_go_back(cloud, timedelta(minutes=10))
    # One core kept fully busy for the ten minutes since.
    kernel_says(cloud, {"host-1": memory(6, cpu_usec=600_000_000)}, {"host-1": sandbox})
    guard(cloud)

    data = page(cloud)
    host = next(h for h in data["hosts"] if h["id"] == str(host_of(cloud, seat).id))
    usage = host["usage"]
    assert usage["memory_bytes"] == 16 * GIB
    assert usage["memory_used_bytes"] == 6 * GIB
    # Its swap file and how pressed its memory was, as the host reported them.
    assert usage["swap_bytes"] == 8 * GIB
    assert usage["swap_used_bytes"] == 1 * GIB
    assert usage["memory_pressure_some_avg10"] == 12.5
    assert usage["memory_pressure_some_avg60"] == 3.0
    assert usage["memory_pressure_full_avg10"] == 4.5
    assert usage["memory_pressure_full_avg60"] == 1.0
    assert usage["zswap"] is False
    assert usage["disk_used_bytes"] == ROOM["bytes"] - ROOM["bytes_free"]
    assert usage["inodes_used"] == ROOM["inodes"] - ROOM["inodes_free"]
    assert usage["cpu_busy"] == pytest.approx(1 / host["cores"], rel=0.01)
    assert usage["sandboxes"] == 1
    assert datetime.fromisoformat(usage["read_at"]) > datetime.now(UTC) - timedelta(
        minutes=1
    )
    # The hours before: both readings are in it, the higher memory last.
    shape = [m for m in usage["memory_history"] if m is not None]
    assert shape == [pytest.approx(4 / 16), pytest.approx(6 / 16)]

    (on_it,) = host["sandboxes"]
    assert on_it["usage"]["memory_bytes"] == GIB
    assert on_it["usage"]["memory_peak_bytes"] == 2 * GIB
    # What of it is the sandbox's own and what is cache, and that it is in no
    # swap and has not been OOM-killed.
    assert on_it["usage"]["anon_bytes"] == 700 * 2**20
    assert on_it["usage"]["file_bytes"] == 300 * 2**20
    assert on_it["usage"]["swap_bytes"] == 0
    assert on_it["usage"]["oom_kills"] == 0
    # What its home holds, as its quota counts it.
    assert on_it["usage"]["disk_bytes"] == 3 * GIB
    assert on_it["usage"]["disk_inodes"] == 40
    assert data["config"]["sandbox_disk_budget_gb"] == (
        settings.cloud_sandbox_disk_budget_gb
    )


def test_a_machine_without_psi_or_swap_leaves_those_blank(cloud):  # noqa: F811
    """A person's computer is read too, and macOS has neither a PSI file nor
    a swap file of ours: the page shows nothing for them rather than a zero
    that reads like an idle swap."""
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-1")
    bare = memory(2, swap_gib=0, swap_used_gib=0, pressure=False)
    bare.pop("swap_bytes")
    bare.pop("swap_free_bytes")
    kernel_says(cloud, {"host-1": bare}, {})

    guard(cloud)

    host = next(
        h for h in page(cloud)["hosts"] if h["id"] == str(host_of(cloud, seat).id)
    )
    usage = host["usage"]
    assert usage["swap_bytes"] is None
    assert usage["swap_used_bytes"] is None
    assert usage["memory_pressure_some_avg10"] is None
    assert usage["memory_pressure_full_avg60"] is None


def test_a_sandbox_destroyed_while_its_host_is_read_does_not_stop_the_guard(
    cloud,  # noqa: F811
):
    """The read takes up to two minutes; a sandbox destroyed meanwhile has no
    row left for its usage. Its host's reading is still kept, and the guard
    goes on to the next machine."""
    first, second, _ = cloud.seats
    working_on(cloud, first, "host-1")
    working_on(cloud, second, "host-2")
    gone = home_of(cloud, first)
    hosts = {"host-1": host_of(cloud, first).id, "host-2": host_of(cloud, second).id}
    kernel_says(
        cloud,
        {"host-1": memory(2), "host-2": memory(3)},
        {"host-1": [{"resource": gone.resource_id, "memory": GIB}]},
    )

    async def destroyed_mid_read(device_id, action):
        if device_id == "host-1" and action == "disk":
            async with cloud.client.test_request_factory() as db:
                await db.delete(await db.get(SandboxHome, gone.id))
                await db.commit()

    cloud.hosts.before = destroyed_mid_read

    assert guard(cloud)["read"] == 2

    rows = {h["id"]: h for h in page(cloud)["hosts"]}
    assert rows[str(hosts["host-1"])]["usage"]["memory_used_bytes"] == 2 * GIB
    assert rows[str(hosts["host-2"])]["usage"]["memory_used_bytes"] == 3 * GIB


#: A disk with its inodes nearly gone: the host takes no new sandbox.
SHORT = {"bytes": 10**12, "bytes_free": 9 * 10**11, "inodes": 2000, "inodes_free": 20}


def test_a_full_host_is_marked_even_when_its_reading_cannot_be_kept(
    cloud,  # noqa: F811
    monkeypatch,
):
    """Keeping the reading is for the page; keeping new sandboxes off a full
    disk is the guard's job, and the first failing must not stop the second."""
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-1")
    kernel_says(cloud, {"host-1": memory(2)}, {}, SHORT)

    async def database_gone(*args, **kwargs):
        raise OperationalError("INSERT", {}, ConnectionError("database went away"))

    monkeypatch.setattr(readings, "record", database_gone)
    guard(cloud)

    assert full_since(cloud, host_of(cloud, seat).id) is not None


def test_a_reading_is_dated_when_the_host_was_read_not_after_the_guard_acted(
    cloud,  # noqa: F811
):
    """Deleting a home left behind can take minutes; the page must not show
    a disk read before that as read after it."""
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-1")
    left = (
        cloud.hosts.home_dir("host-1")
        / ".cheese"
        / "home"
        / str(cloud.project_id)
        / "11111111-1111-4111-8111-111111111111"
    )
    files(left / "room", 3)
    marker = (
        cloud.hosts.home_dir("host-1")
        / ".cheese"
        / "sandboxes"
        / str(cloud.project_id)
        / left.name
    )
    marker.parent.mkdir(parents=True, exist_ok=True)
    # The release its sandbox was started from, which ends what it runs.
    release = cloud.hosts.home_dir("host-1") / "release"
    release.mkdir()
    (release / "cheese-environment.py").write_text("def end_sandbox(home):\n    pass\n")
    marker.write_text(str(release))
    then = time.time() - 3600
    for path in (left, marker):
        os.utime(path, (then, then))
    kernel_says(cloud, {"host-1": memory(2)}, {})
    read: list[float] = []

    async def slow_to_remove(device_id, action):
        if action == "disk":
            read.append(time.time())
        if action == "destroy":
            await asyncio.sleep(1.5)

    cloud.hosts.after = slow_to_remove
    assert guard(cloud)["removed"] == 1

    host = next(
        h for h in page(cloud)["hosts"] if h["id"] == str(host_of(cloud, seat).id)
    )
    stamped = datetime.fromisoformat(host["usage"]["read_at"]).timestamp()
    assert stamped - read[-1] < 1


def test_a_host_whose_sandboxes_are_gone_is_still_read(cloud):  # noqa: F811
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-1")
    host = host_of(cloud, seat).id
    kernel_says(cloud, {"host-1": memory(2)}, {})

    async def gone():
        async with cloud.client.test_request_factory() as db:
            home = await db.scalar(
                select(SandboxHome).where(SandboxHome.session_id == seat.session)
            )
            await db.delete(home)
            await db.commit()

    run(cloud, gone)
    guard(cloud)

    row = next(h for h in page(cloud)["hosts"] if h["id"] == str(host))
    assert row["used"] == 0
    assert row["usage"]["memory_used_bytes"] == 2 * GIB


def test_a_host_short_of_memory_on_two_readings_running_is_reported_once(
    cloud,  # noqa: F811
    alerts,
):
    working_on(cloud, cloud.seats[0], "host-1")
    hostname = host_of(cloud, cloud.seats[0]).hostname
    kernel_says(cloud, {"host-1": memory(15.5)}, {})

    guard(cloud)
    assert posted(cloud, alerts) == []

    readings_go_back(cloud, timedelta(seconds=60))
    guard(cloud)
    (alert,) = posted(cloud, alerts)
    assert hostname in alert and "内存" in alert

    # Still short a minute later: already told.
    readings_go_back(cloud, timedelta(seconds=60))
    guard(cloud)
    assert len(posted(cloud, alerts)) == 1


def test_a_short_host_is_reported_when_its_sandbox_usage_lost_a_race(
    cloud,  # noqa: F811
    alerts,
    monkeypatch,
):
    """Keeping a sandbox's usage can fail when the sandbox goes between the
    check that it exists and the write; the host's own reading and its alert
    still go out."""
    working_on(cloud, cloud.seats[0], "host-1")
    hostname = host_of(cloud, cloud.seats[0]).hostname
    kernel_says(cloud, {"host-1": memory(15.5)}, {})
    guard(cloud)
    readings_go_back(cloud, timedelta(seconds=60))
    real = readings._write
    lost = []

    async def first_write_loses(session, row, usage, now):
        if not lost:
            lost.append(row)
            # A sandbox row gone between the check and the insert.
            try:
                session.add(row)
                await session.execute(insert(SandboxUsage).values(home_id=uuid.uuid4()))
            except IntegrityError:
                await session.rollback()
                return False
        return await real(session, row, usage, now)

    monkeypatch.setattr(readings, "_write", first_write_loses)
    guard(cloud)

    assert lost
    (alert,) = posted(cloud, alerts)
    assert hostname in alert


def test_a_spike_past_the_line_for_one_reading_is_not_reported(
    cloud,  # noqa: F811
    alerts,
):
    working_on(cloud, cloud.seats[0], "host-1")
    machines = {"host-1": memory(4)}
    kernel_says(cloud, machines, {})
    guard(cloud)
    # Past the line for one reading, after one below it, then below again.
    for used in (15.5, 4):
        readings_go_back(cloud, timedelta(seconds=60))
        machines["host-1"] = memory(used)
        guard(cloud)

    assert posted(cloud, alerts) == []


def test_two_hosts_short_at_once_are_each_reported(
    cloud,  # noqa: F811
    alerts,
):
    first, second, _ = cloud.seats
    working_on(cloud, first, "host-1")
    working_on(cloud, second, "host-2")
    names = {host_of(cloud, first).hostname, host_of(cloud, second).hostname}
    assert len(names) == 2
    kernel_says(cloud, {"host-1": memory(15.5), "host-2": memory(15.5)}, {})
    guard(cloud)
    readings_go_back(cloud, timedelta(seconds=60))
    guard(cloud)

    sent = posted(cloud, alerts)
    assert len(sent) == 2
    assert {name for name in names for text in sent if name in text} == names
