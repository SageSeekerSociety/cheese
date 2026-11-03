"""A session's hands, checked a moment ago, are handed out again without
asking the machine or waiting on the room.

The rules, as a person would state them before any of this was built:

- every file tool and command start asks for the session's hands; once the
  machine was checked end to end, the next ask within a minute is answered
  without another round trip to the machine, and without queueing behind
  whatever else holds the room;
- an archived room hands out nothing, however recently its hands were
  checked, and an ask that meets an archival in progress either comes before
  it or is refused — it never leaves a lease the room's cleanup cannot see;
- a call that failed on the machine, a sandbox given up with its host, a
  sandbox being destroyed or not touched for a minute, and a check older
  than a minute all send the next ask through the whole check again.
"""

import json
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

from app.core.device_failures import LinkInterrupted
from app.domain.agent import execution
from app.domain.agent.device_contract import DeviceUnreachable
from app.domain.agent.device_hub import DeviceCallError
from app.domain.agent_session.models import LOST_KEY, AgentSession
from app.domain.agent_session.services import AgentSessionService
from app.domain.machine import session_work as work_lease
from app.domain.machine.models import SandboxHome
from app.domain.topic.models import Topic, TopicStatus
from tests.executor_release import running
from tests.integration.test_sandboxes_on_every_machine import _on_alices_computers
from tests.integration.test_switch_pushes_first import _room

pytestmark = pytest.mark.anyio

INSTALLED = {"state": "/old/state", "workspace": "/old/work", "mcp_servers": []}


def _machine(monkeypatch, *, failing=None):
    """A machine whose executor answers: the calls it was asked, in order."""

    async def answer(target, method, *args, **kwargs):
        if failing is not None and method == "invoke":
            raise failing
        if method == "ping":
            return {**running(), "sandbox": False}
        if method == "prepare":
            return INSTALLED
        return {"device": target["device_id"]}

    remote = AsyncMock(side_effect=answer)
    monkeypatch.setattr(execution, "call", remote)
    monkeypatch.setattr(
        work_lease,
        "device_hub",
        SimpleNamespace(
            target=lambda _device: "linux-amd64",
            is_online=lambda _device: True,
            reconnecting=lambda _device: False,
            exec=AsyncMock(return_value={"exit": 0, "stdout": json.dumps(INSTALLED)}),
        ),
    )
    return remote


def _ask(client, room):
    return client.post(room.lease_path, headers=room.agent, json={"env": {}})


async def _lease(client, room):
    async with client.test_factory() as db:
        return (await AgentSessionService(db).by_id(room.session_id)).work_lease


async def _checked(client, monkeypatch):
    """A room whose session's hands were just checked end to end."""
    room = await _room(client)
    remote = _machine(monkeypatch)
    first = _ask(client, room)
    assert first.status_code == 200, first.text
    assert [call.args[1] for call in remote.await_args_list] == [
        "ping",
        "prepare",
        "ping",
    ]
    remote.reset_mock()
    return room, remote


async def test_hands_checked_a_moment_ago_are_handed_out_without_the_machine(
    client, monkeypatch
):
    room, remote = await _checked(client, monkeypatch)
    before = await _lease(client, room)

    again = _ask(client, room)

    assert again.status_code == 200, again.text
    remote.assert_not_awaited()
    work_lease.device_hub.exec.assert_not_awaited()
    answer = again.json()["data"]
    assert answer["target"]["generation"] == before["generation"]
    assert await _lease(client, room) == before
    # What it hands out works on the machine.
    ran = client.post(
        f"/topics/{room.topic_id}/execution/{room.lease['room_resource_id']}",
        headers={"Authorization": f"Bearer {answer['token']}"},
        json={"method": "invoke", "params": {"id": "call-1", "tool": "Read"}},
    )
    assert ran.status_code == 200, ran.text
    assert ran.json() == {"device": room.old_device}


async def test_they_are_handed_out_while_something_else_holds_the_room(
    client, monkeypatch
):
    """Another admission, a switch or an archival can hold the room's row for
    seconds; an ask for hands already checked does not queue behind it."""
    room, remote = await _checked(client, monkeypatch)
    answered = []
    async with client.test_factory() as holder:
        await holder.execute(
            select(Topic.id)
            .where(Topic.id == room.topic_id)
            .with_for_update(key_share=True)
        )
        asking = threading.Thread(target=lambda: answered.append(_ask(client, room)))
        asking.start()
        asking.join(timeout=15)
        held_through = bool(answered)
        await holder.rollback()
    asking.join()
    assert held_through, "the ask waited for the room's lock"
    assert answered[0].status_code == 200, answered[0].text
    remote.assert_not_awaited()


async def test_an_archived_room_hands_out_nothing(client, monkeypatch):
    room, remote = await _checked(client, monkeypatch)

    archived = client.post(f"/topics/{room.topic_id}/archive", headers=room.person)
    assert archived.status_code == 200, archived.text

    refused = _ask(client, room)
    assert refused.status_code == 409, refused.text
    remote.assert_not_awaited()


async def test_an_ask_during_an_archival_comes_before_it_or_is_refused(
    client, monkeypatch
):
    """The archival holds the room's row and has not committed: the ask is
    answered with the lease already recorded — nothing new for the cleanup
    to miss — and once the archival commits, the next ask is refused."""
    room, remote = await _checked(client, monkeypatch)
    before = await _lease(client, room)
    async with client.test_factory() as archival:
        topic = await archival.scalar(
            select(Topic)
            .where(Topic.id == room.topic_id)
            .with_for_update(key_share=True)
        )
        topic.status = TopicStatus.archived
        await archival.flush()
        during = []
        asking = threading.Thread(target=lambda: during.append(_ask(client, room)))
        asking.start()
        asking.join(timeout=15)
        await archival.commit()
    asking.join()

    assert during[0].status_code == 200, during[0].text
    assert during[0].json()["data"]["target"] == before
    assert await _lease(client, room) == before
    after = _ask(client, room)
    assert after.status_code == 409, after.text
    remote.assert_not_awaited()


@pytest.mark.parametrize(
    "failure",
    [
        DeviceCallError("executor is not running"),
        DeviceUnreachable("dev"),
        LinkInterrupted("dev"),
        TimeoutError("no answer"),
    ],
    ids=["the executor failed", "unreachable", "link dropped mid-call", "no answer"],
)
async def test_a_call_that_failed_on_the_machine_sends_the_next_ask_through_the_check(
    client, monkeypatch, failure
):
    room, _ = await _checked(client, monkeypatch)
    answer = _ask(client, room).json()["data"]
    remote = _machine(monkeypatch, failing=failure)
    failed = client.post(
        f"/topics/{room.topic_id}/execution/{room.lease['room_resource_id']}",
        headers={"Authorization": f"Bearer {answer['token']}"},
        json={"method": "invoke", "params": {"id": "call-1", "tool": "Read"}},
    )
    assert failed.status_code >= 400
    remote.reset_mock()

    again = _ask(client, room)

    assert again.status_code == 200, again.text
    assert remote.await_args_list[0].args[1] == "ping"


async def _change(client, room, **values):
    async with client.test_factory() as db:
        row = await db.get(AgentSession, room.session_id)
        lease, request = dict(row.work_lease), dict(row.execution_request)
        if "checked_ago" in values:
            lease["verified_at"] = (
                datetime.now(UTC) - values["checked_ago"]
            ).isoformat()
        if values.get("lost"):
            request[LOST_KEY] = True
        await db.execute(
            update(AgentSession)
            .where(AgentSession.id == room.session_id)
            .values(work_lease=lease, execution_request=request)
        )
        await db.commit()


@pytest.mark.parametrize(
    "change",
    [
        {"checked_ago": timedelta(minutes=1)},
        {"lost": True},
    ],
    ids=["checked a minute ago", "its sandbox was given up"],
)
async def test_hands_in_doubt_go_through_the_whole_check(client, monkeypatch, change):
    room, remote = await _checked(client, monkeypatch)
    await _change(client, room, **change)

    again = _ask(client, room)

    assert again.status_code == 200, again.text
    assert remote.await_args_list[0].args[1] == "ping"


async def test_a_sandbox_is_handed_out_again_only_while_its_idle_clock_is_fresh(
    client, monkeypatch
):
    """A sandbox's idle clock moves on a tool call at most once a minute; a
    sandbox not touched for that long goes through placement, which moves it,
    so skipping the check never lets the idle sweep take a sandbox in use."""
    room = await _on_alices_computers(client, monkeypatch)
    remote = _machine(monkeypatch)
    again = client.post(room.lease_path, headers=room.agent, json={"env": {}})
    assert again.status_code == 200, again.text
    remote.assert_not_awaited()

    async with client.test_factory() as db:
        await db.execute(
            update(SandboxHome)
            .where(SandboxHome.topic_id == room.topic_id)
            .values(active_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await db.commit()
    touched = client.post(room.lease_path, headers=room.agent, json={"env": {}})
    assert touched.status_code == 200, touched.text
    remote.assert_awaited()
    async with client.test_factory() as db:
        home = await db.scalar(
            select(SandboxHome).where(SandboxHome.topic_id == room.topic_id)
        )
    assert datetime.now(UTC) - home.active_at < timedelta(seconds=30)
