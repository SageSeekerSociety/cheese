"""A machine running sandboxes keeps room on its disk, and its idle sandboxes
give back their memory.

The rules, as a person would state them before any of this was built:

- a machine's disk is checked every minute;
- a host whose disk is nearly full — of bytes, or of inodes, which can run out
  with space still free — takes no new sandbox until it has room again;
- a nearly full disk stops none of the sandboxes on it: each is held to its
  own limit by its quota (``sandbox_host.py``, held by
  ``tests/unit/test_sandbox_network.py``), not by being stopped;
- a sandbox's home outlives no sandbox: one left behind is deleted, and a home
  in use, one just made, or one that is not a sandbox's is not;
- a host takes no more sandboxes than its data disk holds;
- a sandbox whose session has asked for nothing for a while is asked to give
  back its page cache, and one at work is not.

The hosts are directories on this machine and what the platform runs on a
host runs here for real (``test_sandbox_idle_stop.Hosts``). Only the size of
the filesystem they are on is given, since this machine's own disk cannot be
made full.
"""

import json
import os
import re
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.block.models import Block, BlockKind
from app.domain.machine.disk_guard import DiskGuard
from app.domain.machine.models import CloudHost
from app.domain.machine.repositories import CloudHostRepository
from tests.microcloud import OFFERING
from tests.integration.test_cloud_host_pool import pool  # noqa: F401 — fixture
from tests.integration.test_sandbox_idle_stop import (  # noqa: F401 — fixture
    cloud,
    home_of,
    host_comes_up,
    host_of,
    run,
    sandbox_dir,
    tool_call,
    working_on,
)


def disk_of(case, filesystem: dict) -> list[list[str]]:
    """The filesystem the hosts' homes are on is this one, from now on.
    Returns, as readings come, the sandboxes each one asked to give back
    their page cache."""
    real = case.hosts.exec
    idle: list[list[str]] = []

    async def exec_(device_id, argv, *, stdin=None, **kwargs):
        result = await real(device_id, argv, stdin=stdin, **kwargs)
        if stdin and '"action": "disk"' in stdin and not result["exit"]:
            named = re.search(r'"idle": \[([^\]]*)\]', stdin)
            idle.append(re.findall(r'"([0-9a-f-]{36})"', named.group(1)))
            answer = json.loads(result["stdout"].strip().splitlines()[-1])
            answer["filesystem"] = filesystem
            result = {**result, "stdout": json.dumps(answer)}
        return result

    case.hosts.exec = exec_
    return idle


def guard(case) -> dict:
    async def go():
        async with case.client.test_request_factory() as db:
            return await DiskGuard(db, hub=case.hosts).sweep()

    return run(case, go)


def on_the_same_host(case, seat, device_id) -> None:
    """The session's sandbox goes on the host already up as ``device_id``."""
    assert tool_call(case, seat)["target"]["device_id"] == device_id


def files(directory, count: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for n in range(count):
        (directory / f"f{n}").write_text("x")


def said(case, seat) -> list[str]:
    async def read():
        async with case.client.test_request_factory() as db:
            return list(
                await db.scalars(
                    select(Block.content).where(
                        Block.conversation_id == seat.room,
                        Block.kind == BlockKind.event,
                        Block.meta["event_type"].as_string() == "sandbox_asleep",
                    )
                )
            )

    return run(case, read)


def full_since(case, seat_host_id):
    async def read():
        async with case.client.test_request_factory() as db:
            return (await db.get(CloudHost, seat_host_id)).disk_full_since

    return run(case, read)


ROOM = {
    "bytes": 10**12,
    "bytes_free": 9 * 10**11,
    "inodes": 10**9,
    "inodes_free": 9 * 10**8,
}


@pytest.fixture
def two_a_host(monkeypatch):
    monkeypatch.setattr(settings, "cloud_host_slots_per_core", 2)


def test_a_host_short_of_inodes_takes_no_new_sandbox_until_it_has_room(
    cloud,  # noqa: F811
    two_a_host,
):
    first, second, third = cloud.seats
    working_on(cloud, first, "host-1")
    host = host_of(cloud, first).id
    # Space to spare, but under 5% of the inodes free.
    disk_of(
        cloud,
        {
            "bytes": 10**12,
            "bytes_free": 9 * 10**11,
            "inodes": 10**6,
            "inodes_free": 10**4,
        },
    )

    guard(cloud)

    assert full_since(cloud, host) is not None
    assert tool_call(cloud, second).get("preparing")
    assert host_of(cloud, second).id != host

    disk_of(
        cloud,
        {
            "bytes": 10**12,
            "bytes_free": 9 * 10**11,
            "inodes": 10**6,
            "inodes_free": 9 * 10**5,
        },
    )
    guard(cloud)

    assert full_since(cloud, host) is None
    on_the_same_host(cloud, third, "host-1")


def test_a_nearly_full_disk_stops_none_of_its_sandboxes(
    cloud,  # noqa: F811
    two_a_host,
):
    light, heavy, _ = cloud.seats
    light_home = working_on(cloud, light, "host-1")
    on_the_same_host(cloud, heavy, "host-1")
    heavy_home = sandbox_dir(cloud, heavy, "host-1")
    files(heavy_home / ".cheese" / "tmp" / "brute", 300)
    disk_of(
        cloud,
        {"bytes": 10**12, "bytes_free": 10**9, "inodes": 2000, "inodes_free": 20},
    )

    guard(cloud)

    assert heavy_home.exists() and home_of(cloud, heavy) is not None
    assert light_home.exists() and home_of(cloud, light) is not None
    assert said(cloud, heavy) == [] and said(cloud, light) == []
    assert full_since(cloud, host_of(cloud, light).id) is not None


def test_a_home_its_sandbox_left_behind_is_deleted(cloud):  # noqa: F811
    seat = cloud.seats[0]
    kept = working_on(cloud, seat, "host-1")
    machine = cloud.hosts.home_dir("host-1")
    project = str(cloud.project_id)
    # The release a sandbox there was started from, which ends what it runs.
    release = machine / "release"
    release.mkdir()
    (release / "cheese-environment.py").write_text("def end_sandbox(home):\n    pass\n")

    def home(resource: str, *, sandbox: bool, age_s: float):
        path = machine / ".cheese" / "home" / project / resource
        files(path / "room", 3)
        marker = machine / ".cheese" / "sandboxes" / project / resource
        if sandbox:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(str(release))
        then = time.time() - age_s
        for written in (path, marker):
            if written.exists():
                os.utime(written, (then, then))
        return path

    left = home("11111111-1111-4111-8111-111111111111", sandbox=True, age_s=3600)
    fresh = home("22222222-2222-4222-8222-222222222222", sandbox=True, age_s=0)
    rooms_own = home("33333333-3333-4333-8333-333333333333", sandbox=False, age_s=3600)

    assert guard(cloud)["removed"] == 1

    assert not left.exists()
    assert fresh.exists() and rooms_own.exists() and kept.exists()
    assert home_of(cloud, seat) is not None


def test_a_host_takes_no_more_sandboxes_than_its_disk_holds(pool, monkeypatch):  # noqa: F811
    """Cores and memory leave room for two sandboxes, but the data disk, after
    what the host keeps for its projects' stores, holds one."""
    monkeypatch.setattr(
        settings,
        "cloud_sandbox_disk_gb",
        OFFERING["dataDiskGb"] - settings.cloud_host_disk_reserve_gb,
    )
    [alice] = pool.room("alice", 1)
    [bob] = pool.room("bob", 1)
    first = pool.place("alice", alice)
    pool.up(first)

    assert pool.place("bob", bob) != first


def test_a_full_host_is_still_read_after_its_last_sandbox_goes(cloud):  # noqa: F811
    """The mark comes off once the disk has room, even with no sandbox left on
    the host to bring it back into the reading."""
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-1")
    host = host_of(cloud, seat).id
    disk_of(cloud, {"bytes": 10**12, "bytes_free": 10**9, "inodes": 0})
    guard(cloud)
    assert full_since(cloud, host) is not None

    async def gone():
        async with cloud.client.test_request_factory() as db:
            repo = CloudHostRepository(db)
            await repo.delete_home(await repo.current_home(seat.session))
            await db.commit()

    run(cloud, gone)
    disk_of(cloud, {"bytes": 10**12, "bytes_free": 9 * 10**11, "inodes": 0})
    guard(cloud)

    assert full_since(cloud, host) is None


def idle_for(case, seat, minutes: int) -> None:
    """The session has asked for no tool for ``minutes``."""

    async def go():
        async with case.client.test_request_factory() as db:
            home = await CloudHostRepository(db).current_home(seat.session)
            home.active_at = datetime.now(UTC) - timedelta(minutes=minutes)
            await db.commit()

    run(case, go)


def test_an_idle_sandbox_gives_back_its_page_cache_and_one_at_work_does_not(
    cloud,  # noqa: F811
    two_a_host,
):
    resting, busy, _ = cloud.seats
    working_on(cloud, resting, "host-1")
    on_the_same_host(cloud, busy, "host-1")
    idle_for(cloud, resting, 5)
    asked = disk_of(cloud, ROOM)

    guard(cloud)

    assert asked == [[home_of(cloud, resting).resource_id]]
