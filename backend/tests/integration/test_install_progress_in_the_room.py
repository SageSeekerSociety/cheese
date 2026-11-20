"""While the AI teammate's runtime downloads onto a person's own computer, the
room it works in is told how far the download has got.

The first time a teammate works on a computer, the install fetches a runtime
of a hundred megabytes and more, and the tool call that asked for the computer
waits on it. Before this, the room saw nothing for as long as that took — a
tester waited nine minutes asking whether anything was happening. What must
hold: one line in the room, rewritten in place as the download advances (never
a stream of new lines), ending as installed or, cut off, as how far it got —
and the next attempt continues that same line. An install that downloads
nothing, or a connector that does not stream, says nothing and still installs.
"""

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.models import Block
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.machine import install_progress
from app.domain.machine import session_work as work_lease
from app.domain.topic.models import Topic
from tests.integration.conftest import post_project, registered, session_auth_headers
from tests.support.devices import enrollment_team, place_in_project_team

pytestmark = pytest.mark.anyio

MB = 1_000_000
STILL_GOING = (
    "downloading the executor from https://example.test/claude: still going "
    "after 120.0 MB of 240.0 MB, which the next attempt continues from"
)
INSTALLED = {
    "exit": 0,
    "stdout": json.dumps(
        {"state": "/w/state", "workspace": "/w/work", "mcp_servers": []}
    ),
}


async def _room_on_alices_computer(client):
    project = post_project(client, json={"name": "Orchard"}, owner="alice").json()[
        "data"
    ]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Pricing"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    async with client.test_factory() as db:
        owner_id = await registered(db, "alice")
        devices = sql_device_service(db)
        device = await devices.approve(
            await devices.start("alice-laptop"),
            owner_user_id=owner_id,
            supply=Supply.self_hosted,
            team_id=await enrollment_team(devices, owner_id),
        )
        await place_in_project_team(devices, device.device_id, project_id)
        agent = await IdentityService(db).ensure_room_agent_user(topic_id)
        topic = await db.get(Topic, topic_id)
        resource = str(topic.resource_id or topic_id)
        session = await AgentSessionService(db).ensure(
            topic_id, agent.username, harness="claude-code"
        )
        session.execution_request = {"generation": str(uuid.uuid4())}
        await AgentSessionService(db).set_environment(
            topic_id,
            agent.username,
            {"kind": "self_hosted", "device_id": device.device_id},
            chosen_by=None,
        )
        session.runtime_location = {
            "device_id": "center",
            "resource_id": resource,
            "channel": "device",
        }
        token = bind_resource_token(
            mint_scoped_token(
                project_id=str(project_id),
                topic_id=str(topic_id),
                agent_handle=agent.username,
            ),
            resource,
            session_id=str(session.id),
        )
        await db.commit()
    return SimpleNamespace(
        topic_id=topic_id,
        lease_path=f"/topics/{topic_id}/sessions/{session.id}/work-lease",
        agent={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
def room_frames(monkeypatch):
    """Every frame the pages open on a conversation are sent, as they go."""
    monkeypatch.setattr(install_progress, "SHOW_EVERY_S", 0)
    broker = get_broker()
    sent: list[tuple[str, dict]] = []
    publish = broker.publish

    async def recording(channel, frame):
        sent.append((str(channel), frame))
        return await publish(channel, frame)

    monkeypatch.setattr(broker, "publish", recording)
    return sent


def _install_lines(sent, topic_id):
    """The install's line as the room's pages received it, frame by frame."""
    return [
        frame["block"]
        for channel, frame in sent
        if channel == str(topic_id)
        and frame.get("type") in {"event_block", "block_updated"}
        and (frame["block"].get("meta") or {}).get("event_type") == "executor_install"
    ]


def _downloaded(have, total=240 * MB):
    return json.dumps({"executor_download": {"have": have, "total": total}})


def _computer(monkeypatch, install):
    monkeypatch.setattr(
        work_lease,
        "device_hub",
        SimpleNamespace(
            target=lambda _device: "linux-amd64",
            is_online=lambda d: True,
            exec=AsyncMock(side_effect=install),
        ),
    )
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={}))


def _downloading(sent, topic_id, *, until: str):
    """An install that writes its download progress, giving the room a moment
    after each line to be shown it, then answers ``until``."""

    async def install(device, argv, *, on_stderr=None, **kwargs):
        for have in (0, 60 * MB, 120 * MB, 240 * MB):
            if until == "cut" and have == 240 * MB:
                break
            if on_stderr is not None:
                on_stderr(_downloaded(have))
            shown = f"{have // MB} / 240 MB"
            for _ in range(200):
                lines = _install_lines(sent, topic_id)
                if lines and shown in lines[-1]["content"]:
                    break
                await asyncio.sleep(0.01)
        if until == "cut":
            # Killed at the install's deadline, as the connector reports it:
            # the download's sentence for whoever reads the failure, and its
            # last progress line after it, as a kill between the two leaves it.
            return {
                "exit": work_lease.NO_EXIT_STATUS,
                "stderr": f"{STILL_GOING}\n{_downloaded(120 * MB)}\n",
            }
        return INSTALLED

    return install


async def _lines_in_room(client, topic_id):
    async with client.test_factory() as db:
        return list(
            await db.scalars(
                select(Block).where(
                    Block.conversation_id == topic_id,
                    Block.meta["event_type"].as_string() == "executor_install",
                )
            )
        )


async def test_the_room_watches_one_line_advance_until_the_runtime_is_installed(
    client, monkeypatch, room_frames
):
    room = await _room_on_alices_computer(client)
    _computer(monkeypatch, _downloading(room_frames, room.topic_id, until="done"))

    leased = client.post(room.lease_path, headers=room.agent, json={"env": {}})

    assert leased.status_code == 200, leased.text
    assert "target" in leased.json()["data"], leased.text
    seen = _install_lines(room_frames, room.topic_id)
    assert seen, "the room was told nothing about the download"
    # One line, rewritten in place: every frame is the same block.
    assert len({line["id"] for line in seen}) == 1
    shown = [line["content"] for line in seen]
    assert "alice-laptop" in shown[0] and "0 / 240 MB" in shown[0]
    assert any("120 / 240 MB" in content for content in shown)
    assert seen[-1]["meta"]["state"] == "installed"
    assert "MB" not in shown[-1]
    assert len(await _lines_in_room(client, room.topic_id)) == 1


async def test_a_cut_off_download_says_how_far_it_got_and_the_next_attempt_continues_the_line(
    client, monkeypatch, room_frames
):
    room = await _room_on_alices_computer(client)
    _computer(monkeypatch, _downloading(room_frames, room.topic_id, until="cut"))

    cut = client.post(room.lease_path, headers=room.agent, json={"env": {}})

    assert "target" not in (cut.json().get("data") or {}), cut.text
    # The teammate is told how far it got in words, never the progress line.
    assert STILL_GOING in cut.text
    assert "executor_download" not in cut.text
    lines = _install_lines(room_frames, room.topic_id)
    assert lines, "the room was told nothing about the download"
    stopped = lines[-1]
    assert stopped["meta"]["state"] == "stopped"
    assert "120 / 240 MB" in stopped["content"]

    _computer(monkeypatch, _downloading(room_frames, room.topic_id, until="done"))
    leased = client.post(room.lease_path, headers=room.agent, json={"env": {}})

    assert "target" in leased.json()["data"], leased.text
    seen = _install_lines(room_frames, room.topic_id)
    assert {line["id"] for line in seen} == {stopped["id"]}
    assert seen[-1]["meta"]["state"] == "installed"
    assert len(await _lines_in_room(client, room.topic_id)) == 1


async def test_a_connector_that_does_not_stream_still_installs_and_says_nothing(
    client, monkeypatch, room_frames
):
    room = await _room_on_alices_computer(client)

    async def install(device, argv, **kwargs):
        # A connector built before streaming: the lines are never sent up.
        return INSTALLED

    _computer(monkeypatch, install)

    leased = client.post(room.lease_path, headers=room.agent, json={"env": {}})

    assert "target" in leased.json()["data"], leased.text
    assert _install_lines(room_frames, room.topic_id) == []
    assert await _lines_in_room(client, room.topic_id) == []


async def test_a_download_heard_only_as_the_install_ends_still_installs_and_says_so(
    client, monkeypatch, room_frames
):
    """The line can be first written already settled — when writing it while
    the download ran failed, say. The install still answers as it went. Here
    the answer comes before the line's writer has had a turn."""
    room = await _room_on_alices_computer(client)

    async def install(device, argv, *, on_stderr=None, **kwargs):
        on_stderr(_downloaded(0))
        on_stderr(_downloaded(240 * MB))
        return INSTALLED

    _computer(monkeypatch, install)

    leased = client.post(room.lease_path, headers=room.agent, json={"env": {}})

    assert "target" in leased.json()["data"], leased.text
    lines = await _lines_in_room(client, room.topic_id)
    assert [line.meta["state"] for line in lines] == ["installed"]


async def test_an_install_whose_line_cannot_be_written_still_installs(
    client, monkeypatch, room_frames
):
    """The line only shows how the install goes: when the room cannot be
    written to, the install that worked still hands over its machine."""
    room = await _room_on_alices_computer(client)

    async def unwritable(*args, **kwargs):
        raise RuntimeError("the database is away")

    async def install(device, argv, *, on_stderr=None, **kwargs):
        # Nothing reaches the room here, so there is nothing to wait for.
        for have in (0, 120 * MB, 240 * MB):
            on_stderr(_downloaded(have))
            await asyncio.sleep(0)
        return INSTALLED

    monkeypatch.setattr(install_progress.InstallProgress, "_write", unwritable)
    _computer(monkeypatch, install)

    leased = client.post(room.lease_path, headers=room.agent, json={"env": {}})

    assert leased.status_code == 200, leased.text
    assert "target" in leased.json()["data"], leased.text
