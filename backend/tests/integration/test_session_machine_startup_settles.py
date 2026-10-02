"""A session machine's startup record in its room ends, one way or the other.

The room shows the Cloud startup as one row that counts 「已等待」 until the
record ends. For a session's machine it used to stop at
「连接器安装完成，等待平台确认连接」 and count for as long as anyone looked,
while the machine was connected and running the session's commands.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.block.repositories import BlockRepository
from app.domain.machine import progress
from app.domain.machine.models import AiStatus, MachineStatus
from app.domain.machine.repositories import ProjectMachineRepository
from app.domain.machine.services import MachineService
from tests.integration.conftest import post_project

ENROLLING_DONE = "连接器安装完成，等待平台确认连接"


@pytest.fixture
def room(client, monkeypatch):
    monkeypatch.setattr(progress, "async_session_factory", client.test_request_factory)
    project_id = post_project(client, json={"name": "Startup record"}).json()["data"][
        "id"
    ]
    topic_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "Room"},
    ).json()["data"]["id"]
    return uuid.UUID(project_id), uuid.UUID(topic_id)


def _machine(client, project_id, topic_id, *, device_id, hostname, enrolled_at=None):
    async def add():
        async with client.test_request_factory() as db:
            repo = ProjectMachineRepository(db)
            machine = await repo.add(
                project_id=project_id,
                topic_id=topic_id,
                session_id=uuid.uuid4(),
                machine_id=1,
                customer_id=1,
                account_id=1,
                offering_id=1,
                hostname=hostname,
                login_user="cheese",
                cores=2,
                memory_mb=4096,
                disk_gb=20,
                status=MachineStatus.running,
                ip="192.0.2.1",
                requested_by="user-1",
                ai_status=AiStatus.disabled,
            )
            await repo.mark_enrolled(
                machine,
                device_id=device_id,
                when=enrolled_at or datetime.now(UTC),
            )
            await db.commit()
            return machine.id

    return client.portal.call(add)


def _startup(client, topic_id, machine_id, text):
    client.portal.call(
        lambda: progress.startup_progress(topic_id, text, machine_id=machine_id)
    )


def _room_events(client, topic_id):
    async def read():
        async with client.test_request_factory() as db:
            return [
                (block.content, (block.meta or {}).get("state"))
                for block in await BlockRepository(db).list_for_topic(topic_id)
                if (block.meta or {}).get("event_type")
                in ("cloud_startup", "cloud_provisioning")
            ]

    return client.portal.call(read)


def _settle(client, online, now=None):
    """One pass of the enrollment sweep's settling step, as it runs it."""

    async def sweep():
        async with client.test_request_factory() as db:
            startups = await MachineService(db).unsettled_startups(now=now)
            await db.commit()
        await progress.settle_startups(
            startups, lambda device: device in online, now=now
        )

    client.portal.call(sweep)


def test_a_connected_machine_ends_the_record_once(client, room):
    project_id, topic_id = room
    machine = _machine(client, project_id, topic_id, device_id="dev-a", hostname="a")
    _startup(client, topic_id, machine, ENROLLING_DONE)

    _settle(client, online={"dev-a"})
    _settle(client, online={"dev-a"})

    assert _room_events(client, topic_id) == [
        (ENROLLING_DONE, None),
        (progress.CONNECTED, "ready"),
    ]


def test_a_machine_that_never_connects_is_given_up_on_once(client, room):
    project_id, topic_id = room
    machine = _machine(client, project_id, topic_id, device_id="dev-a", hostname="a")
    _startup(client, topic_id, machine, ENROLLING_DONE)

    _settle(client, online=set())
    assert _room_events(client, topic_id) == [(ENROLLING_DONE, None)]

    later = datetime.now(UTC) + progress.CONNECT_GRACE + timedelta(seconds=1)
    _settle(client, online=set(), now=later)
    _settle(client, online=set(), now=later)
    events = _room_events(client, topic_id)
    assert [state for _, state in events] == [None, "failed"]

    # It turning up afterwards is news: the room was told it was not coming.
    _settle(client, online={"dev-a"})
    assert [state for _, state in _room_events(client, topic_id)] == [
        None,
        "failed",
        "ready",
    ]


def test_one_machine_does_not_end_another_ones_startup(client, room):
    project_id, topic_id = room
    first = _machine(client, project_id, topic_id, device_id="dev-a", hostname="a")
    _startup(client, topic_id, first, ENROLLING_DONE)
    _settle(client, online={"dev-a"})
    second = _machine(client, project_id, topic_id, device_id="dev-b", hostname="b")
    _startup(client, topic_id, second, ENROLLING_DONE)

    _settle(client, online={"dev-a"})
    assert _room_events(client, topic_id)[-1] == (ENROLLING_DONE, None)

    _settle(client, online={"dev-a", "dev-b"})
    assert _room_events(client, topic_id)[-1] == (progress.CONNECTED, "ready")
    assert len(_room_events(client, topic_id)) == 4
