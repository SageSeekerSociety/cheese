"""An idle cloud sandbox is frozen, and its session's next tool call wakes the
same one.

Freezing is the default (`cloud_sandbox_freeze`): an idle sandbox on one of the
platform's hosts is checkpointed once, its processes are stopped, it is allowed
the host's swap and asked to give back what memory the kernel can take. It
keeps its host's slot, its home and its work, and it is not billed. Its
session's next tool call wakes this very sandbox — same host, same home, same
processes — instead of building a new one.

It cannot be kept for good: past `cloud_sandbox_frozen_keep_s` it is destroyed
as any idle sandbox always was; a slot the pool needs, a disk running short and
a host being rebuilt take it sooner. A whole cloud VM and a sandbox on a
person's own computer are not frozen — they keep the lifecycle their own files
pin.

The hosts are the ones of ``test_sandbox_idle_stop``: what the platform runs on
a host (``machine/sandbox_home.py``) runs here for real, with ``HOME`` pointed
at the host's directory. This machine has no sandbox helper
(``/usr/local/libexec/cheese-sandbox``), so a helper command is a no-op
there — which is the case the freeze has to get right: no swap file to use, no
memory to reclaim, and still a frozen sandbox.
"""

import json
import shutil
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.domain.agent_session.models import THAW_KEY, AgentSession
from app.domain.machine import metering, sandbox_home
from app.domain.machine import session_work as work_lease
from app.domain.machine.disk_guard import DiskGuard
from app.domain.machine.models import CloudHost, SandboxHome
from app.domain.machine.runner import SandboxSweeper
from app.domain.machine.services import HostPool
from app.domain.usage.compute import SANDBOX, ComputeMeter
from app.domain.usage.ledger import Ledger, payer_for_project
from tests.integration.test_sandbox_idle_stop import cloud as cloud
from tests.integration.test_sandbox_idle_stop import (
    host_of,
    run,
    sandbox_dir,
    time_passes,
    tool_call,
    working_on,
)


@pytest.fixture
def icy(cloud, monkeypatch):
    """The fixture's rooms with freezing on: the one it borrows pins the old
    destroy (`test_sandbox_idle_stop`), since that is what its own tests
    measure."""
    monkeypatch.setattr(settings, "cloud_sandbox_freeze", True)
    return cloud


def freeze_sweep(case) -> dict:
    return run(case, SandboxSweeper(case.client.test_request_factory).sweep)


def home_row(case, seat) -> SandboxHome | None:
    async def read():
        async with case.client.test_request_factory() as db:
            return await db.scalar(
                select(SandboxHome).where(SandboxHome.session_id == seat.session)
            )

    return run(case, read)


def actions(case) -> list[tuple[str, str]]:
    return list(case.hosts.actions)


def frozen_for(case, seat, by: timedelta) -> None:
    """The sandbox has been frozen for ``by``."""

    async def age():
        async with case.client.test_request_factory() as db:
            await db.execute(
                update(SandboxHome)
                .where(SandboxHome.session_id == seat.session)
                .values(frozen_at=datetime.now(UTC) - by)
            )
            await db.commit()

    run(case, age)


def holds(case, seat, device_id: str) -> bool:
    async def read():
        async with case.client.test_request_factory() as db:
            return await HostPool(db, case.hosts).holds(seat.session, device_id)

    return run(case, read)


def a_seat_working(case):
    """One session with a sandbox on a host of its own, frozen once idle.
    Returns it, its home and its host's device."""

    seat = case.seats[0]
    working_on(case, seat, "host-1")
    device = host_of(case, seat).device_id
    home = sandbox_dir(case, seat, device)
    time_passes(case, seat, timedelta(minutes=20))
    case.hosts.actions.clear()
    assert freeze_sweep(case)["destroyed"] == 0
    assert home_row(case, seat).frozen_at is not None
    assert home.exists(), "the frozen sandbox's work was taken with it"
    return seat, home, device


def test_an_idle_sandbox_is_frozen_not_destroyed(icy):
    seat, home, device = a_seat_working(icy)

    # Its home, its work and its host are still the session's.
    assert home.exists()
    assert (home / "room" / "notes.md").read_text() == "not committed anywhere\n"
    current = home_row(icy, seat)
    assert current is not None and current.frozen_at is not None
    assert (current.device_id or host_of(icy, seat).device_id) == device
    # Its processes were stopped on the host, and the host was asked to have
    # what it could of its memory back. No helper here: the swap and reclaim
    # commands answer nothing, and the freeze stands anyway.
    asked = [action for (_, action) in actions(icy)]
    assert "freeze" in asked
    assert "destroy" not in asked


def test_a_frozen_sandbox_is_not_billed(icy):
    seat, _, _ = a_seat_working(icy)
    home_id = home_row(icy, seat).id

    async def observe():
        async with icy.client.test_request_factory() as db:
            meter = ComputeMeter(db)
            await meter.open(
                kind=SANDBOX,
                spec=SANDBOX,
                subject=str(home_id),
                project_id=icy.project_id,
                topic_id=seat.room,
                session_id=seat.session,
                at=datetime.now(UTC) - timedelta(hours=1),
            )
            await db.commit()
            await metering.observe(db)
            return [run.subject for run in await ComputeMeter(db).open_runs(SANDBOX)]

    # A run that started while it was running is closed when it is frozen; a
    # sweep while it is frozen opens none.
    assert str(home_id) not in run(icy, observe)


def test_with_freezing_off_an_idle_sandbox_is_destroyed_as_before(icy, monkeypatch):
    monkeypatch.setattr(settings, "cloud_sandbox_freeze", False)
    seat = icy.seats[0]
    home = working_on(icy, seat, "host-1")
    time_passes(icy, seat, timedelta(minutes=20))

    assert freeze_sweep(icy)["destroyed"] == 1
    assert not home.exists()
    assert home_row(icy, seat) is None


def test_a_frozen_sandbox_is_not_held_out(icy):
    seat, _, device = a_seat_working(icy)

    assert not holds(icy, seat, device)


def test_a_tool_call_thaws_the_same_sandbox_and_its_work_is_there(icy):
    seat, home, device = a_seat_working(icy)
    before = home_row(icy, seat)
    assert before is not None
    host_id, resource = before.host_id, before.resource_id

    answer = tool_call(icy, seat, tells_agent=True)

    # Same host, same home, same processes.
    assert answer["target"]["device_id"] == device
    # Waking is not losing: nothing was destroyed, so the agent is told
    # nothing about it. A notice here would say it is in a new sandbox.
    assert "notice" not in answer
    after = home_row(icy, seat)
    assert after is not None
    assert (after.host_id, after.resource_id, after.frozen_at) == (
        host_id,
        resource,
        None,
    )
    asked = [action for (_, action) in actions(icy)]
    # Woken, not rebuilt: let it swap again and resume what was frozen.
    assert "thaw" in asked
    assert "destroy" not in asked
    assert (home / "room" / "notes.md").exists()


def test_a_frozen_sandbox_past_its_keep_time_is_destroyed(icy):
    seat, home, _ = a_seat_working(icy)
    frozen_for(icy, seat, timedelta(seconds=settings.cloud_sandbox_frozen_keep_s + 60))

    assert freeze_sweep(icy)["destroyed"] == 1
    assert home_row(icy, seat) is None
    assert not home.exists()


def test_a_frozen_sandbox_is_kept_within_its_keep_time(icy):
    seat, home, _ = a_seat_working(icy)
    frozen_for(icy, seat, timedelta(seconds=settings.cloud_sandbox_frozen_keep_s - 60))

    assert freeze_sweep(icy)["destroyed"] == 0
    assert home_row(icy, seat) is not None
    assert home.exists()


def test_no_place_evicts_the_frozen_sandbox_frozen_longest_ago(icy, monkeypatch):
    oldest, newer, waiting = icy.seats
    # One host of two slots, the whole pool: the two sessions fill it, and a
    # third has nowhere to go but a freed slot.
    monkeypatch.setattr(settings, "cloud_host_slots_per_core", 2)
    monkeypatch.setattr(settings, "cloud_pool_max_hosts", 1)
    working_on(icy, oldest, "host-1")
    assert tool_call(icy, newer)["target"]["device_id"] == "host-1"
    time_passes(icy, oldest, timedelta(minutes=20))
    time_passes(icy, newer, timedelta(minutes=20))
    assert freeze_sweep(icy)["destroyed"] == 0
    frozen_for(icy, oldest, timedelta(minutes=30))
    frozen_for(icy, newer, timedelta(minutes=10))

    # The pool's one host is full of frozen sandboxes: the one frozen longest
    # ago gives up its slot, and the waiting session starts on that host —
    # not on one the pool would have to create.
    answer = tool_call(icy, waiting)
    assert answer.get("target", {}).get("device_id") == "host-1", answer
    assert home_row(icy, oldest) is None
    assert home_row(icy, newer) is not None
    assert len(icy.provider.machines) == 1


def test_a_tool_call_that_lands_while_it_is_frozen_never_leaves_it_frozen(icy):
    """The session asks for its hands while the freeze is on its way to the
    host. That call marks the home awake again; the freeze sees it when it
    comes back and thaws the sandbox, so it is not left frozen and the next
    call finds it running."""
    seat = icy.seats[0]
    working_on(icy, seat, "host-1")
    time_passes(icy, seat, timedelta(minutes=20))
    woke: list[bool] = []

    async def during(device_id, action):
        if action != "freeze" or woke:
            return
        # What `HostPool.place` does under the pool's lock for a frozen home:
        # clear the mark, move the idle clock, and ask for the thaw. A tool
        # call cannot be made from here — the sweep holds this loop — and this
        # is the whole of what it changes before the freeze locks again.
        async with icy.client.test_request_factory() as db:
            home = await db.scalar(
                select(SandboxHome)
                .where(SandboxHome.session_id == seat.session)
                .with_for_update()
            )
            assert home.frozen_at is not None
            home.frozen_at = None
            home.active_at = datetime.now(UTC)
            row = await db.get(AgentSession, seat.session)
            row.execution_request = {
                **(row.execution_request or {}),
                THAW_KEY: home.resource_id,
            }
            await db.commit()
        woke.append(True)

    icy.hosts.before = during
    try:
        assert freeze_sweep(icy)["destroyed"] == 0
    finally:
        icy.hosts.before = None

    assert woke
    # Not left frozen: the freeze undone on the host.
    current = home_row(icy, seat)
    assert current is not None and current.frozen_at is None
    asked = [action for (_, action) in actions(icy)]
    assert "thaw" in asked
    assert "destroy" not in asked

    # The next tool call is the one that asked: same sandbox, on the host.
    target = tool_call(icy, seat)["target"]
    assert target["device_id"] == "host-1"
    after = home_row(icy, seat)
    assert after is not None and after.frozen_at is None
    assert (after.host_id, after.resource_id) == (
        current.host_id,
        current.resource_id,
    )


def test_a_frozen_sandbox_is_not_checkpointed_on_its_way_out(icy, monkeypatch):
    """The room's cleanup checkpoints its sessions before stopping them. A
    frozen sandbox was checkpointed before it slept and nothing ran since: its
    stopped processes could not run `cheese sync` now, so it is skipped."""
    seat = icy.seats[0]
    working_on(icy, seat, "host-1")
    time_passes(icy, seat, timedelta(minutes=20))
    assert freeze_sweep(icy)["destroyed"] == 0
    assert home_row(icy, seat).frozen_at is not None

    async def lease_ready():
        async with icy.client.test_request_factory() as db:
            row = await db.get(AgentSession, seat.session)
            row.work_lease = {
                "kind": "device",
                "platform": True,
                "session_id": str(seat.session),
                "device_id": "host-1",
                "status": "ready",
                "state": "/state",
            }
            await db.commit()

    run(icy, lease_ready)
    pushed: list = []

    async def record(lease, start):
        pushed.append(lease)
        return True

    monkeypatch.setattr(work_lease, "checkpoint", record)
    icy.hosts.installs.clear()

    async def go():
        async with icy.client.test_request_factory() as db:
            await work_lease.checkpoint_room(db, seat.room)

    run(icy, go)

    assert pushed == []
    assert icy.hosts.installs == []


def _disk(case, filesystem: dict) -> None:
    """What every host's disk reading answers from now on."""

    real = case.hosts.exec

    async def exec_(device_id, argv, *, stdin=None, **kwargs):
        result = await real(device_id, argv, stdin=stdin, **kwargs)
        if stdin and '"action": "disk"' in stdin and not result["exit"]:
            answer = json.loads(result["stdout"].strip().splitlines()[-1])
            answer["filesystem"] = filesystem
            result = {**result, "stdout": json.dumps(answer)}
        return result

    case.hosts.exec = exec_


def guard(case) -> dict:
    async def go():
        async with case.client.test_request_factory() as db:
            return await DiskGuard(db, hub=case.hosts).sweep()

    return run(case, go)


def test_a_host_short_of_disk_lets_a_frozen_sandbox_go_first(icy):
    seat, home, _ = a_seat_working(icy)
    _disk(
        icy,
        {
            "bytes": 10**12,
            "bytes_free": 10**8,
            "inodes": 10**9,
            "inodes_free": 10**8,
        },
    )

    guard(icy)

    assert home_row(icy, seat) is None
    assert not home.exists()


def rebuild_sweep(case) -> None:
    """The pool's sweep (`reaper.maintain`), which picks a host to rebuild,
    then the sandbox sweep, which lets a rebuilt host's frozen sandboxes go."""

    async def go():
        async with case.client.test_request_factory() as db:
            await HostPool(db, case.provider, hub=case.hosts).maintain()
            await db.commit()

    run(case, go)
    # The sandbox sweep is what lets a draining host's frozen sandboxes go.
    run(case, SandboxSweeper(case.client.test_request_factory).sweep)


def test_a_host_being_rebuilt_lets_its_frozen_sandbox_go_at_once(icy, monkeypatch):
    seat, home, device = a_seat_working(icy)
    # The pool's floor is two hosts: the frozen sandbox's host is rebuilt only
    # once the other one has room for what it holds.
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 2)
    rebuild_sweep(icy)
    assert home_row(icy, seat) is not None

    async def older():
        async with icy.client.test_request_factory() as db:
            await db.execute(
                update(CloudHost)
                .where(CloudHost.device_id == device)
                .values(image_version="a host made before today")
            )
            await db.commit()

    run(icy, older)
    rebuild_sweep(icy)

    # Frozen means "not in use": the sandbox goes at once, without waiting for
    # the host to drain as an active one would.
    assert home_row(icy, seat) is None
    assert not home.exists()

    async def draining():
        async with icy.client.test_request_factory() as db:
            return await db.scalar(
                select(CloudHost.draining_since).where(CloudHost.device_id == device)
            )

    assert run(icy, draining) is not None


def test_a_frozen_sandbox_goes_when_the_credits_run_out(icy, monkeypatch):
    seat, home, _ = a_seat_working(icy)

    async def spend():
        async with icy.client.test_request_factory() as db:
            payer = await payer_for_project(db, icy.project_id)
            await Ledger(db).charge(payer, 10_000)
            await db.commit()

    run(icy, spend)
    # Whether a payer still has credits is asked once a minute per process.
    monkeypatch.setattr(metering, "_checked_at", None)

    assert freeze_sweep(icy)["destroyed"] == 1
    assert home_row(icy, seat) is None
    assert not home.exists()


def test_deleting_a_frozen_sandbox_thaws_it_before_stopping_it(tmp_path):
    """The host program's destroy runs the helper's `thaw` before it asks the
    session's executor to stop. A frozen executor cannot answer that request:
    its processes are stopped, so a destroy that sent it first would wait for
    a reply only a thaw can allow."""
    home = tmp_path / "home"
    home.mkdir()
    helper = tmp_path / "cheese-sandbox"
    helper.write_text("")
    order: list[str] = []

    def run_command(argv, **_kwargs):
        order.append(argv[3])
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    def stop_executor(home, resource):
        assert order == ["thaw"], "the executor was stopped while still frozen"
        order.append("stop")

    cleanup = {
        "SANDBOX_HOST": str(helper),
        "run_command": run_command,
        "resource_paths": lambda base, project, resource: (home, tmp_path / "work"),
        "stop_executor": stop_executor,
        "end_holders": lambda paths: None,
        "check_no_writers": lambda paths: None,
        "remove_tree": shutil.rmtree,
        "resource_tmp": lambda resource: None,
        "sandbox_marker": lambda home: tmp_path / "marker",
    }

    assert sandbox_home.destroy(cleanup, "project", "resource") == {
        "destroyed": True,
        "processes": [],
    }
    assert order == ["thaw", "stop"]
    assert not home.exists()
