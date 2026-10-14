"""A machine running sandboxes keeps room on its disk.

The rules, as a person would state them before any of this was built:

- a machine's free space is checked every minute, and what its sandboxes'
  homes hold (a walk of every file) only every few minutes, except that a
  check finding the disk below a floor walks at once, so the sandbox stopped is
  the one holding the most now;
- a host whose disk is nearly full — of bytes, or of inodes, which can run out
  with space still free — takes no new sandbox until it has room again;
- the sandbox holding the most of a nearly full disk is stopped, and its room
  is told what it held and where, so its agent can tell what to stop leaving
  behind; the other sandboxes there keep running;
- a disk filled by something other than the sandboxes stops none of them;
- on a platform host, a sandbox holding more than one sandbox may is stopped
  on the next walk of its homes, even while the disk has room, and its room is
  told the limit; on a person's computer there is no such limit;
- a sandbox's home outlives no sandbox: one left behind is deleted, and a home
  in use, one just made, or one that is not a sandbox's is not;
- a host takes no more sandboxes than its disk holds.

The hosts are directories on this machine and what the platform runs on a
host runs here for real (``test_sandbox_idle_stop.Hosts``): the homes are
walked and counted. Only the size of the filesystem they are on is given,
since this machine's own disk cannot be made full.
"""

import json
import os
import re
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.block.models import Block, BlockKind
from app.domain.device.models import DeviceRow
from app.domain.device.supply import Supply
from app.domain.machine import disk_guard
from app.domain.machine.disk_guard import DiskGuard
from app.domain.machine.models import CloudHost
from app.domain.machine.repositories import CloudHostRepository
from app.domain.user.repositories import UserRepository
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


def disk_of(case, filesystem: dict) -> list[bool]:
    """The filesystem the hosts' homes are on is this one, from now on.
    Returns, as readings come, whether each one walked the homes."""
    real = case.hosts.exec
    walks: list[bool] = []

    async def exec_(device_id, argv, *, stdin=None, **kwargs):
        result = await real(device_id, argv, stdin=stdin, **kwargs)
        if stdin and '"action": "disk"' in stdin and not result["exit"]:
            walks.append('"walk": true' in stdin)
            answer = json.loads(result["stdout"].strip().splitlines()[-1])
            answer["filesystem"] = filesystem
            result = {**result, "stdout": json.dumps(answer)}
        return result

    case.hosts.exec = exec_
    return walks


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


@pytest.fixture(autouse=True)
def no_walk_yet():
    """Every test starts with no machine walked in this process."""
    disk_guard._walked.clear()
    yield
    disk_guard._walked.clear()


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


def test_the_heaviest_sandbox_is_stopped_and_its_room_told_what_filled_it(
    cloud,  # noqa: F811
    two_a_host,
):
    light, heavy, _ = cloud.seats
    light_home = working_on(cloud, light, "host-1")
    on_the_same_host(cloud, heavy, "host-1")
    heavy_home = sandbox_dir(cloud, heavy, "host-1")
    files(heavy_home / ".cheese" / "tmp" / "brute", 300)
    files(light_home / "room", 5)
    disk_of(
        cloud,
        {"bytes": 10**12, "bytes_free": 9 * 10**11, "inodes": 2000, "inodes_free": 20},
    )

    assert guard(cloud)["evicted"] == 1

    assert home_of(cloud, heavy) is None
    assert not heavy_home.exists()
    assert home_of(cloud, light) is not None and light_home.exists()
    [line] = said(cloud, heavy)
    assert "/tmp/brute" in line
    held = re.search(r"([0-9][0-9,]*) 个文件", line)
    assert held and int(held.group(1).replace(",", "")) >= 300
    assert said(cloud, light) == []


def test_a_disk_the_sandboxes_hardly_use_stops_none_of_them(cloud):  # noqa: F811
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-1")
    # Nearly full, of something that is not the sandboxes'.
    disk_of(
        cloud,
        {"bytes": 10**13, "bytes_free": 10**9, "inodes": 10**9, "inodes_free": 10**6},
    )

    assert guard(cloud)["evicted"] == 0

    assert home_of(cloud, seat) is not None and home.exists()
    assert full_since(cloud, host_of(cloud, seat).id) is not None


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
    """Cores and memory leave room for two sandboxes, but the disk, after
    what the host keeps for itself, holds one."""
    monkeypatch.setattr(
        settings,
        "cloud_sandbox_disk_gb",
        settings.microcloud_default_disk_gb - settings.cloud_host_disk_reserve_gb,
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


def test_the_homes_are_walked_only_every_few_minutes(cloud, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "sandbox_disk_walk_s", 300)
    working_on(cloud, cloud.seats[0], "host-1")
    walks = disk_of(cloud, ROOM)

    guard(cloud)
    guard(cloud)
    guard(cloud)
    assert walks == [True, False, False]

    monkeypatch.setattr(settings, "sandbox_disk_walk_s", 0)  # the interval has passed
    guard(cloud)
    assert walks[-1] is True


def test_a_check_finding_the_disk_short_walks_at_once_and_stops_the_heaviest(
    cloud,  # noqa: F811
    two_a_host,
    monkeypatch,
):
    monkeypatch.setattr(settings, "sandbox_disk_walk_s", 300)
    light, heavy, _ = cloud.seats
    working_on(cloud, light, "host-1")
    on_the_same_host(cloud, heavy, "host-1")
    disk_of(cloud, ROOM)
    guard(cloud)  # walked, with nothing to act on
    # Between walks, the heavy one fills the disk.
    files(sandbox_dir(cloud, heavy, "host-1") / ".cheese" / "tmp" / "brute", 300)
    walks = disk_of(cloud, {**ROOM, "inodes": 2000, "inodes_free": 20})

    result = guard(cloud)

    assert walks == [False, True]
    assert result["evicted"] == 1
    assert home_of(cloud, heavy) is None and home_of(cloud, light) is not None


def test_a_sandbox_over_its_budget_on_a_host_is_stopped_while_the_disk_has_room(
    cloud,  # noqa: F811
    two_a_host,
    monkeypatch,
):
    monkeypatch.setattr(settings, "cloud_sandbox_inode_budget", 200)
    light, heavy, _ = cloud.seats
    light_home = working_on(cloud, light, "host-1")
    on_the_same_host(cloud, heavy, "host-1")
    files(sandbox_dir(cloud, heavy, "host-1") / ".cheese" / "tmp" / "brute", 300)
    disk_of(
        cloud,
        {
            "bytes": 10**12,
            "bytes_free": 9 * 10**11,
            "inodes": 10**9,
            "inodes_free": 9 * 10**8,
        },
    )

    assert guard(cloud)["evicted"] == 1

    assert home_of(cloud, heavy) is None
    assert home_of(cloud, light) is not None and light_home.exists()
    [line] = said(cloud, heavy)
    assert "/tmp/brute" in line and "200 个文件" in line


def test_on_a_persons_computer_a_sandbox_has_no_budget(cloud, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "cloud_sandbox_inode_budget", 200)
    seat = cloud.seats[0]
    resource = "44444444-4444-4444-8444-444444444444"

    async def on_alices_computer():
        async with cloud.client.test_request_factory() as db:
            alice = await UserRepository(db).get_by_handle("alice")
            db.add(
                DeviceRow(
                    device_id="alices-computer",
                    name="alices-computer",
                    token="alices-token",
                    owner_user_id=alice.id,
                    supply=Supply.self_hosted,
                    created_at=datetime.now(UTC),
                )
            )
            await db.flush()
            await CloudHostRepository(db).put_device_home(
                "alices-computer",
                {
                    "spec": "standard",
                    "project_id": cloud.project_id,
                    "topic_id": seat.room,
                    "room_resource_id": str(seat.room),
                    "resource_id": resource,
                    "session_id": seat.session,
                },
            )
            await db.commit()

    run(cloud, on_alices_computer)
    home = (
        cloud.hosts.home_dir("alices-computer")
        / ".cheese"
        / "home"
        / str(cloud.project_id)
        / resource
    )
    files(home / ".cheese" / "tmp" / "brute", 300)
    cloud.hosts.online.add("alices-computer")
    disk_of(
        cloud,
        {
            "bytes": 10**12,
            "bytes_free": 9 * 10**11,
            "inodes": 10**9,
            "inodes_free": 9 * 10**8,
        },
    )

    assert guard(cloud) == {"read": 1, "walked": 1, "evicted": 0, "removed": 0}

    assert home.exists() and home_of(cloud, seat) is not None


def test_a_sandbox_is_held_to_its_budget_on_the_walk_not_between_walks(
    cloud,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setattr(settings, "sandbox_disk_walk_s", 300)
    monkeypatch.setattr(settings, "cloud_sandbox_inode_budget", 200)
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-1")
    walks = disk_of(cloud, ROOM)
    guard(cloud)  # walked, within its budget
    files(home / ".cheese" / "tmp" / "brute", 300)

    assert guard(cloud)["evicted"] == 0  # a check between walks
    assert home_of(cloud, seat) is not None

    monkeypatch.setattr(settings, "sandbox_disk_walk_s", 0)  # the interval has passed
    assert guard(cloud)["evicted"] == 1
    assert walks == [True, False, True]
    assert home_of(cloud, seat) is None
