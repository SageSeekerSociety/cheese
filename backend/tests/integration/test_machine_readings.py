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
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core import alerting
from app.core.config import settings
from app.domain.machine import disk_guard
from app.domain.machine.models import HostReading, SandboxHome
from tests.integration.conftest import session_auth_headers
from tests.integration.test_cloud_host_pool import pool  # noqa: F401 — fixture
from tests.integration.test_sandbox_disk_guard import ROOM, files, guard
from tests.integration.test_sandbox_idle_stop import (  # noqa: F401 — fixture
    cloud,
    home_of,
    host_of,
    run,
    sandbox_dir,
    working_on,
)

GIB = 2**30


def kernel_says(case, machines: dict[str, dict], sandboxes: dict[str, list]) -> None:
    """From now on each host's reading reports this of its memory and CPU,
    and these sandbox cgroups; its disk is ``ROOM``."""
    real = case.hosts.exec

    async def exec_(device_id, argv, *, stdin=None, **kwargs):
        result = await real(device_id, argv, stdin=stdin, **kwargs)
        if stdin and '"action": "disk"' in stdin and not result["exit"]:
            answer = json.loads(result["stdout"].strip().splitlines()[-1])
            answer["filesystem"] = ROOM
            answer["machine"] = machines.get(device_id, {})
            answer["sandboxes"] = sandboxes.get(device_id, [])
            result = {**result, "stdout": json.dumps(answer)}
        return result

    case.hosts.exec = exec_


def memory(used_gib: float, *, total_gib: int = 16, cpu_usec: int = 0) -> dict:
    return {
        "memory": total_gib * GIB,
        "memory_available": int((total_gib - used_gib) * GIB),
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
    disk_guard._walked.clear()
    yield
    disk_guard._walked.clear()


def test_the_page_shows_what_each_host_and_its_sandboxes_use(cloud):  # noqa: F811
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-1")
    files(home / "build", 40)
    resource = home_of(cloud, seat).resource_id
    sandbox = [{"resource": resource, "memory": GIB, "memory_peak": 2 * GIB}]
    kernel_says(cloud, {"host-1": memory(4, cpu_usec=0)}, {"host-1": sandbox})
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
    # Its home was walked on the first reading: the files it left are counted.
    assert on_it["usage"]["disk_inodes"] >= 40
    assert data["config"]["sandbox_disk_budget_gb"] == (
        settings.cloud_sandbox_disk_budget_gb
    )


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


def test_a_spike_past_the_line_for_one_reading_is_not_reported(
    cloud,  # noqa: F811
    alerts,
):
    working_on(cloud, cloud.seats[0], "host-1")
    machines = {"host-1": memory(15.5)}
    kernel_says(cloud, machines, {})
    guard(cloud)
    readings_go_back(cloud, timedelta(seconds=60))
    machines["host-1"] = memory(4)
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
