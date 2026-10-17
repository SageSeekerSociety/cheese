"""A room that asks for the sandbox it already has gets another machine, and a
machine whose disk is full stops being handed new sandboxes.

FB-101, on dev on 2026-10-16: a Cloud host's disk filled up, so every tool call
of the session on it was answered 502 — the ``prepare`` each call makes was
refused for want of space, and the machine's refusal reached the room as an
error. Two things were wrong. Asking for the sandbox the session already had was
a no-op, so a room could not get off the host by asking; and a host whose disk
was full went on being offered to sessions, so the next call landed on it again.

The hosts, the pool and the tool call here are the ones of
``test_sandbox_idle_stop``: a tool call asks for its hands the way the session's
client does.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import settings
from app.core.device_failures import DeviceCallError
from app.domain.agent import execution
from app.domain.device.models import DeviceRow
from app.domain.device.supply import Supply
from app.domain.identity.services import IdentityService
from app.domain.machine import session_work as work_lease
from app.domain.machine.models import CloudHost
from tests.executor_release import running
from tests.integration.test_sandbox_idle_stop import cloud as cloud
from tests.integration.test_sandbox_idle_stop import (
    home_of,
    host_of,
    run,
    tool_call,
    working_on,
)

STANDARD = {"kind": "sandbox", "spec": "standard"}

#: What the machine answers an install with when its disk is full. ``[Errno 28]``
#: is what ``ExecutorSetupFailed.reason`` reads, and ``ENOSPC`` is what the
#: ``OSError`` of a write that found no room carries.
NO_ROOM = (
    "OSError: [Errno 28] No space left on device: '.cheese-platform-skills.json.next'"
)


def _two_rooms_on_two_hosts(cloud, monkeypatch) -> tuple[str, str]:
    """One room working on each of two hosts, and each host with a slot left
    over. A host that runs one sandbox is no use here: a session that leaves one
    still holds its slot until the sweep destroys it, so the host it left could
    not take it back anyway, and neither rule below would be under test. The
    rooms are put on hosts of one sandbox each first, and the hosts' size is
    raised after: a host with a slot free is what placement offers first."""
    working_on(cloud, cloud.seats[0], "host-a")
    working_on(cloud, cloud.seats[1], "host-b")
    monkeypatch.setattr(settings, "cloud_host_slots_per_core", 2)
    return "host-a", "host-b"


def _enrolled(cloud, *device_ids: str) -> None:
    """The machines' device rows, as enrolling a host leaves behind
    (``HostPool.enroll``). A lease whose machine has no device row is dropped by
    the next attempt as one of a machine that was unbound, so without these the
    attempt after a host is left never dials anything and the lease's machine is
    never read again."""
    onward = cloud.seats[0].session.hex[:8]

    async def seed():
        async with cloud.client.test_request_factory() as db:
            owner = await IdentityService(db).ensure_agent_user(
                handle=f"host-owner-{onward}"
            )
            for device_id in device_ids:
                db.add(
                    DeviceRow(
                        device_id=device_id,
                        name=device_id,
                        token=f"token-{device_id}",
                        owner_user_id=owner.id,
                        supply=Supply.cloud,
                        created_at=datetime.now(UTC),
                    )
                )
            await db.commit()

    run(cloud, seed)


def _is_full(cloud, device_id: str) -> bool:
    async def read():
        async with cloud.client.test_request_factory() as db:
            host = await db.scalar(
                select(CloudHost).where(CloudHost.device_id == device_id)
            )
            return host is not None and host.disk_full_since is not None

    return run(cloud, read)


def _hands(cloud, seat, tries: int = 3) -> str:
    """The machine the session's next tool call works on, waiting out however
    many installations have not answered yet."""
    answer = None
    for _ in range(tries):
        answer = tool_call(cloud, seat)
        if "target" in answer:
            return answer["target"]["device_id"]
        assert "unavailable" not in answer, answer
    raise AssertionError(f"the session got no hands: {answer}")


def test_a_room_asked_for_the_sandbox_it_has_is_moved_to_another_machine(
    cloud, monkeypatch
):
    """Asking for one of the platform's sandboxes when the session already has
    one is how a room gets off the machine it is on. It was answered as a
    request for what it had, so nothing moved, and the room stayed on the host
    it was asking to leave."""
    _two_rooms_on_two_hosts(cloud, monkeypatch)
    seat = cloud.seats[0]

    moved = cloud.client.put(
        f"/topics/{seat.room}/sessions/{seat.session}/environment",
        headers={"X-Cheese-Token": seat.token},
        json={"environment": STANDARD},
    )

    assert moved.status_code == 200, moved.text
    # What the session had on that machine is nobody's now: the idle sweep
    # destroys it like any sandbox (`lifecycle`).
    assert home_of(cloud, seat) is None
    # Its next call is answered by the machine it moved to, not the one it left
    # — that one has a slot free and is the pool's first choice besides, since
    # placement prefers the fullest host so the others can empty.
    assert _hands(cloud, seat) == "host-b"


def test_the_machine_left_for_its_disk_is_not_dialed_again(cloud, monkeypatch):
    """The attempt after the full host is left keeps the lease of the machine
    just left: it names that machine, and dialing it answers with that machine's
    own refusal again, which ``_install`` records against the host this attempt
    was placed on. The healthy host was marked as one that ran out of space and
    the session's call failed, over a machine that had nothing wrong with it."""
    # What the whole check does each time the hands are asked for again: the
    # window in which hands just checked are handed out unchecked is not
    # what this is about (`session_work.VERIFIED_FOR`).
    monkeypatch.setattr(work_lease, "VERIFIED_FOR", timedelta(0))
    _two_rooms_on_two_hosts(cloud, monkeypatch)
    _enrolled(cloud, "host-a", "host-b")
    seat = cloud.seats[0]
    full = host_of(cloud, seat).id
    dialed: list[tuple[str, str]] = []

    async def call(target, method, _params, **_kwargs):
        device = target["device_id"]
        dialed.append((device, method))
        if method == "ping":
            # A running executor of this release, in a sandbox: the attempt
            # prepares it in place rather than installing one — which is how
            # the machine just left gets dialed again.
            return {**running(), "sandbox": True}
        if device == "host-a":
            raise DeviceCallError(NO_ROOM)
        return {}

    monkeypatch.setattr(execution, "call", call)

    answering = tool_call(cloud, seat)

    assert "unavailable" not in answering, answering
    assert answering["target"]["device_id"] == "host-b", "the session stayed put"
    # The host that was left was asked for its hands once, not again on the
    # attempt that was placed on the other one.
    assert dialed == [("host-a", "ping"), ("host-a", "prepare"), ("host-b", "ping")]
    assert host_of(cloud, seat).id != full
    assert not _is_full(cloud, "host-b"), "a healthy host was marked as full"


def test_a_machine_that_ran_out_of_space_is_left_and_takes_no_new_sandbox(
    cloud, monkeypatch
):
    """The host of the session's sandbox had no room, so the install there
    could not get through. The session is placed on another host and works on,
    and no new sandbox is offered the full host until a reading finds room
    again — the disk guard's own mark (`disk_full_since`, read and cleared
    every minute)."""
    # What the whole check does each time the hands are asked for again: the
    # window in which hands just checked are handed out unchecked is not
    # what this is about (`session_work.VERIFIED_FOR`).
    monkeypatch.setattr(work_lease, "VERIFIED_FOR", timedelta(0))
    _two_rooms_on_two_hosts(cloud, monkeypatch)
    seat, other, third = cloud.seats
    full = host_of(cloud, seat).id

    real = cloud.hosts.exec

    async def exec_(device, argv, *, stdin=None, **kwargs):
        # Only the install: the host program the sweep runs is a different
        # command, and it still has to work.
        if device == "host-a" and stdin and "def main(cleanup, request)" not in stdin:
            return {"exit": 1, "stdout": "", "stderr": NO_ROOM}
        return await real(device, argv, stdin=stdin, **kwargs)

    monkeypatch.setattr(cloud.hosts, "exec", exec_)

    # The room is not handed the machine's error: the failed install is one the
    # session is placed away from, and its call is answered by another host —
    # within the call, since the attempt that failed is retried while the caller
    # still waits (`ensure`).
    answering = tool_call(cloud, seat)

    assert "unavailable" not in answering, answering
    assert answering["target"]["device_id"] == "host-b", "the session stayed put"
    assert host_of(cloud, seat).id != full
    # And the host it left takes nobody: it has a slot free, so placement would
    # offer it — this third room's sandbox goes to another host, a new one the
    # pool brings up, rather than the one whose disk just refused a write.
    third_gets = tool_call(cloud, third)
    landed = host_of(cloud, third)
    assert landed is not None, third_gets
    assert landed.id != full, "the host whose disk was full was offered again"
    # The room that was already on the other host keeps it.
    assert _hands(cloud, other) == "host-b"
