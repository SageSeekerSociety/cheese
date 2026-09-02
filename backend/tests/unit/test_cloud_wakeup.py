"""A Cloud topic's held message is delivered the moment its machine can take it."""

import uuid

import pytest

from app.domain.machine.wakeup import CloudWakeup

pytestmark = pytest.mark.anyio


def _wakeup(*, leases, waiting, online):
    log: list[tuple[str, uuid.UUID]] = []

    async def ready_leases(device_id):
        return [(t, d) for t, d in leases if d == device_id]

    async def waiting_topics(topic_ids):
        return [t for t in topic_ids if t in waiting]

    async def kickoff(topic_id):
        log.append(("kickoff", topic_id))

    async def announce(topic_id):
        log.append(("announce", topic_id))

    return (
        CloudWakeup(
            ready_leases=ready_leases,
            waiting_topics=waiting_topics,
            kickoff=kickoff,
            announce=announce,
            is_online=lambda device_id: device_id in online,
        ),
        log,
    )


async def test_only_a_connected_machine_with_a_waiting_room_is_woken():
    waiting_on_online = uuid.uuid4()
    waiting_on_offline = uuid.uuid4()
    already_told = uuid.uuid4()
    leases = [
        (waiting_on_online, "dev-on"),
        (waiting_on_offline, "dev-off"),
        (already_told, "dev-on"),
    ]
    wakeup, log = _wakeup(
        leases=leases,
        waiting={waiting_on_online, waiting_on_offline},
        online={"dev-on"},
    )

    await wakeup.wake(leases)

    assert log == [("kickoff", waiting_on_online), ("announce", waiting_on_online)]


async def test_a_device_attaching_wakes_the_lease_waiting_on_it_and_no_other():
    mine = uuid.uuid4()
    someone_elses = uuid.uuid4()
    leases = [(mine, "dev-1"), (someone_elses, "dev-2")]
    wakeup, log = _wakeup(
        leases=leases, waiting={mine, someone_elses}, online={"dev-1", "dev-2"}
    )

    await wakeup.wake_device("dev-1")

    assert [t for _, t in log] == [mine, mine]


async def test_nothing_ready_means_nothing_asked_or_said():
    wakeup, log = _wakeup(leases=[], waiting=set(), online=set())
    await wakeup.wake([])
    await wakeup.wake_device("dev-1")
    assert log == []
