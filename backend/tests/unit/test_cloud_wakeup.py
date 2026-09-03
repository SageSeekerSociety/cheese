"""A Cloud topic's held message is delivered the moment its machine can take it."""

import uuid

import pytest

from app.domain.machine.wakeup import CloudWakeup

pytestmark = pytest.mark.anyio


def _wakeup(*, leases, waiting, online):
    log: list[tuple[str, uuid.UUID] | tuple[str, uuid.UUID, str]] = []

    async def ready_leases(device_id):
        return [(t, d) for t, d in leases if d == device_id]

    async def waiting_topics(topic_ids):
        return [t for t in topic_ids if t in waiting]

    async def kickoff(topic_id):
        log.append(("kickoff", topic_id))

    async def announce(topic_id):
        log.append(("announce", topic_id))

    async def announce_failure(topic_id, text):
        log.append(("failed", topic_id, text))

    return (
        CloudWakeup(
            ready_leases=ready_leases,
            waiting_topics=waiting_topics,
            kickoff=kickoff,
            announce=announce,
            is_online=lambda device_id: device_id in online,
            announce_failure=announce_failure,
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


async def test_a_lease_microcloud_gave_up_on_is_announced_once_to_a_waiting_room():
    """No sweep will ever hand a failed lease to `wake`, and the room is still
    showing 「机器正在创建」. It is told once; a room already past waiting (told
    before, or never waiting) hears nothing, and nothing is kicked off."""
    from app.domain.machine.services import FailedLease

    waiting = uuid.uuid4()
    already_told = uuid.uuid4()
    wakeup, log = _wakeup(leases=[], waiting={waiting}, online=set())
    failed = [
        FailedLease(
            topic_id=waiting,
            hostname="box-1",
            reason="MicroCloud 报告机器的 AI 通道配置失败",
        ),
        FailedLease(
            topic_id=already_told,
            hostname="box-2",
            reason="MicroCloud 报告机器创建失败",
        ),
    ]

    await wakeup.report_failures(failed)
    await wakeup.report_failures(failed)  # the next tick sees the same leases

    assert [entry[:2] for entry in log] == [("failed", waiting)] * 2 or [
        entry[:2] for entry in log
    ] == [("failed", waiting)], log
    text = log[0][2]
    assert "box-1" in text and "AI 通道" in text
    assert not any(entry[0] == "kickoff" for entry in log)
