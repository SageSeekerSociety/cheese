"""A conversation follows its session to the computer it starts on.

A seat's conversation is a set of files in its room's home on the computer its
session ran on: a transcript per working directory that wrote one, and beside
each the subagents' transcripts and saved tool results. When the session starts
on another computer, or in a reopened room's new home, those files are carried
there through the private bucket and the conversation goes on. When they cannot
be (the computer they are on is away, they are too large, a process is still
writing them, a file does not arrive as it left), the session starts a new
conversation and the room is told why.

Each computer here is a home directory of its own, and every command the
platform sends one runs there as it would through its connector.
"""

import asyncio
import io
import os
import subprocess
import sys
import tarfile
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.domain.agent import session_carry
from app.domain.agent.carry_files import FOOTPRINT_ROOT
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.channel import mint_session_token
from app.domain.agent.owner_provider import OwnerChannel
from app.domain.agent.place import seat_key, seat_name
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.session_record import service as session_records
from app.domain.topic.models import Topic
from tests.integration.conftest import post_project, registered, session_auth_headers
from tests.integration.test_kept_room_files import Bucket
from tests.integration.test_own_agent_machine import _own_session
from tests.support.devices import enrollment_team
from tests.support.environments import remember_place

pytestmark = pytest.mark.anyio

AGENT = "cheese"
CENTER = "center"
TOKEN = "11111111-1111-1111-1111-111111111111"


class Computers:
    """The connectors of several computers: each runs what it is sent in its
    own home, and answers whether it is connected."""

    def __init__(self, root: Path, *devices: str) -> None:
        self.homes = {device: root / device for device in devices}
        for home in self.homes.values():
            home.mkdir(parents=True, exist_ok=True)
        self.away: set[str] = set()

    def is_online(self, device):
        return device in self.homes and device not in self.away

    def reconnecting(self, _device):
        return False

    def target(self, _device):
        return "linux-amd64"

    def all_online_screens(self):
        return []

    async def exec(self, device_id, argv, *, stdin=None, timeout=60, env=None, **_):
        assert argv[:2] == ["python3", "-"], argv
        done = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-", *argv[2:]],
            input=stdin,
            text=True,
            capture_output=True,
            # The bucket is on loopback, past any proxy the environment names.
            env={
                **os.environ,
                **(env or {}),
                "HOME": str(self.homes[device_id]),
                "no_proxy": "*",
            },
            timeout=timeout,
        )
        return {"exit": done.returncode, "stdout": done.stdout, "stderr": done.stderr}

    def projects(self, device, project, resource) -> Path:
        """A room home's shared conversation directory on ``device``."""
        return (
            self.homes[device]
            / FOOTPRINT_ROOT
            / "home"
            / str(project)
            / str(resource)
            / ".claude"
            / "projects"
        )


@pytest.fixture
def bucket(monkeypatch):
    answering = Bucket()
    monkeypatch.setattr(session_carry, "private_storage", lambda: answering.storage)
    yield answering
    answering.close()


def _conversation(projects: Path, sid: str = TOKEN) -> dict[str, bytes]:
    """A conversation written under two working directories, with a subagent's
    transcript, a saved tool result, and the link Claude Code leaves to a
    subagent's original when it resumes; and another conversation beside it."""
    files = {
        f"-work-a/{sid}.jsonl": b'{"type":"user","n":1}\n',
        f"-work-b/{sid}.jsonl": b'{"type":"assistant","n":2}\n',
        f"-work-a/{sid}/subagents/agent-1.jsonl": b'{"type":"user","sub":1}\n',
        f"-work-a/{sid}/tool-results/result-1.txt": b"a long tool result\n",
    }
    for path, data in files.items():
        (projects / path).parent.mkdir(parents=True, exist_ok=True)
        (projects / path).write_bytes(data)
    linked = projects / f"-work-b/{sid}/subagents/agent-1.jsonl"
    linked.parent.mkdir(parents=True, exist_ok=True)
    linked.symlink_to(projects / f"-work-a/{sid}/subagents/agent-1.jsonl")
    files[f"-work-b/{sid}/subagents/agent-1.jsonl"] = files[
        f"-work-a/{sid}/subagents/agent-1.jsonl"
    ]
    (projects / "-work-a/someone-else.jsonl").write_bytes(b"not this one\n")
    return files


def _held(projects: Path) -> dict[str, bytes]:
    return {
        p.relative_to(projects).as_posix(): p.read_bytes()
        for p in projects.rglob("*")
        if p.is_file()
    }


def _ref(project=None):
    return SessionRef(
        project or uuid.uuid4(), uuid.uuid4(), AGENT, harness="claude-code"
    )


async def _carry(computers, session, origin, target):
    return await session_carry.carry(
        computers,
        session,
        TOKEN,
        origin=session_carry.Where(*origin),
        target=session_carry.Where(*target),
        agent=AGENT,
    )


async def test_every_piece_of_the_conversation_arrives_as_it_was(tmp_path, bucket):
    computers = Computers(tmp_path, "old", "new")
    session = _ref()
    before, after = str(uuid.uuid4()), str(uuid.uuid4())
    sent = _conversation(computers.projects("old", session.project_id, before))

    assert await _carry(computers, session, ("old", before), ("new", after)) is None

    assert _held(computers.projects("new", session.project_id, after)) == sent


async def test_a_conversation_another_room_already_holds_differently_stays_unchanged(
    tmp_path, bucket
):
    computers = Computers(tmp_path, "old", "new")
    session = _ref()
    before, after = str(uuid.uuid4()), str(uuid.uuid4())
    _conversation(computers.projects("old", session.project_id, before))
    there = computers.projects("new", session.project_id, after)
    (there / "-work-a").mkdir(parents=True)
    (there / f"-work-a/{TOKEN}.jsonl").write_bytes(b"other bytes\n")

    answer = await _carry(computers, session, ("old", before), ("new", after))

    assert answer == session_carry.FAILED
    assert _held(there) == {f"-work-a/{TOKEN}.jsonl": b"other bytes\n"}


async def test_a_link_out_of_the_room_is_not_followed(tmp_path, bucket):
    computers = Computers(tmp_path, "old", "new")
    session = _ref()
    before, after = str(uuid.uuid4()), str(uuid.uuid4())
    projects = computers.projects("old", session.project_id, before)
    _conversation(projects)
    secret = tmp_path / "old" / "secret.txt"
    secret.write_text("the owner's own file")
    (projects / f"-work-a/{TOKEN}/subagents/agent-2.jsonl").symlink_to(secret)

    answer = await _carry(computers, session, ("old", before), ("new", after))

    assert answer == session_carry.FAILED
    assert bucket.puts == 0
    assert not computers.projects("new", session.project_id, after).exists()


async def test_a_claude_code_still_writing_it_keeps_it_where_it_is(tmp_path, bucket):
    computers = Computers(tmp_path, "old", "new")
    session = _ref()
    before, after = str(uuid.uuid4()), str(uuid.uuid4())
    _conversation(computers.projects("old", session.project_id, before))
    seat = (
        computers.homes["old"]
        / FOOTPRINT_ROOT
        / "home"
        / str(session.project_id)
        / before
        / FOOTPRINT_ROOT
        / "seats"
        / seat_name(seat_key(AGENT))
        / ".claude"
    )
    # A process that names itself as Claude Code does, working in the seat.
    claude = tmp_path / "bin" / "claude"
    claude.parent.mkdir()
    claude.symlink_to(sys.executable)
    running = subprocess.Popen(
        [str(claude), "-c", "import time; time.sleep(30)"],
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(seat)},
    )
    try:
        # Past its exec: until then the process is still this test's python.
        # Linux names it by its file's name, macOS by its path.
        while (
            Path(
                subprocess.run(
                    ["ps", "-o", "comm=", "-p", str(running.pid)],
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            ).name
            != "claude"
        ):
            await asyncio.sleep(0.05)
        answer = await _carry(computers, session, ("old", before), ("new", after))
    finally:
        running.kill()
        running.wait()

    assert answer == session_carry.FAILED
    assert bucket.puts == 0


async def test_one_over_the_size_a_carry_takes_is_not_sent(
    tmp_path, bucket, monkeypatch
):
    monkeypatch.setattr(session_carry, "CARRY_MAX_BYTES", 16)
    computers = Computers(tmp_path, "old", "new")
    session = _ref()
    before, after = str(uuid.uuid4()), str(uuid.uuid4())
    _conversation(computers.projects("old", session.project_id, before))

    answer = await _carry(computers, session, ("old", before), ("new", after))

    assert answer == session_carry.TOO_LARGE
    assert bucket.puts == 0


async def test_a_computer_that_is_away_is_not_waited_for(tmp_path, bucket):
    computers = Computers(tmp_path, "old", "new")
    computers.away.add("old")
    session = _ref()

    answer = await _carry(
        computers, session, ("old", str(uuid.uuid4())), ("new", str(uuid.uuid4()))
    )

    assert answer == session_carry.OFFLINE


async def test_a_file_that_did_not_arrive_as_it_left_is_not_laid_down(tmp_path, bucket):
    computers = Computers(tmp_path, "new")
    project, resource = uuid.uuid4(), str(uuid.uuid4())
    key = "carry/test.tar.gz"
    url = await bucket.storage.presign(key, "put_object", 60)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        data = b"changed on the way\n"
        info = tarfile.TarInfo(f"-work-a/{TOKEN}.jsonl")
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
    urllib.request.urlopen(
        urllib.request.Request(url, data=archive.getvalue(), method="PUT")
    ).read()

    with pytest.raises(RuntimeError, match="did not arrive as it was sent"):
        await session_carry._run(
            computers,
            "new",
            {
                "action": "unpack",
                "project": str(project),
                "resource": resource,
                "manifest": [
                    {
                        "path": f"-work-a/{TOKEN}.jsonl",
                        "size": 19,
                        "sha256": "0" * 64,
                    }
                ],
                "url": await bucket.storage.presign(key, "get_object", 60),
            },
        )
    assert not computers.projects("new", project, resource).exists()


# --- through the channel ------------------------------------------------------


@pytest.fixture
def room(client):
    project = post_project(client, json={"name": "Carried"}, owner="alice").json()[
        "data"
    ]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    return uuid.UUID(project["id"]), uuid.UUID(topic["id"])


def _central(client, computers, monkeypatch) -> CentralChannel:
    monkeypatch.setattr(settings, "agent_session_device_id", CENTER)
    return CentralChannel(
        DeviceChannel(hub=computers, session_factory=client.test_request_factory)
    )


def _start(client, central, session):
    async def start():
        precheck = await central.precheck(session, needs_place=False)
        token = mint_session_token(
            session.project_id, session.topic_id, precheck.agent_handle
        )
        async with central.prepare_session(
            session=session, token=token, env={}, precheck=precheck
        ) as prepared:
            return prepared

    return client.portal.call(start)


async def _reopened(client, central, project, topic) -> tuple[str, str]:
    """The room's seat had conversation ``TOKEN`` on the session host, and the
    room was archived and reopened: a new generation, with a new home there."""
    async with client.test_factory() as db:
        room = await db.get(Topic, topic)
        before = str(room.resource_id or room.id)
        sessions = AgentSessionService(db)
        await sessions.remember(
            conversation_id=topic,
            agent_handle=AGENT,
            resume_token=TOKEN,
            harness="claude-code",
            generation=0,
        )
        await remember_place(
            db,
            conversation_id=topic,
            agent_handle=AGENT,
            environment=None,
            runtime_location={
                "device_id": CENTER,
                "resource_id": before,
                "channel": central.name,
                "runtime": {"agent_handle": AGENT, "state": "/somewhere"},
            },
            harness="claude-code",
        )
        room.resource_id = uuid.uuid4()
        await sessions.reopen_room(topic)
        await db.commit()
        return before, str(room.resource_id)


def _told(client, topic) -> list[dict]:
    blocks = client.get(
        f"/topics/{topic}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return [
        b
        for b in blocks
        if (b.get("meta") or {}).get("event_type") == "conversation_not_carried"
    ]


async def _continues(client, topic) -> str | None:
    async with client.test_factory() as db:
        return (
            await AgentSessionService(db).continuation(
                topic, AGENT, harness="claude-code", generation=0
            )
        ).resume


async def test_a_reopened_room_goes_on_with_its_conversation(
    client, room, tmp_path, bucket, monkeypatch
):
    project, topic = room
    computers = Computers(tmp_path, CENTER)
    central = _central(client, computers, monkeypatch)
    before, after = await _reopened(client, central, project, topic)
    sent = _conversation(computers.projects(CENTER, project, before))

    started = _start(
        client, central, SessionRef(project, topic, AGENT, harness="claude-code")
    )

    assert started.retired is None
    assert _held(computers.projects(CENTER, project, after)) == sent
    assert await _continues(client, topic) == TOKEN
    assert _told(client, topic) == []


async def test_a_reopened_room_whose_conversation_is_gone_starts_a_new_one_and_says_so(
    client, room, tmp_path, bucket, monkeypatch
):
    project, topic = room
    computers = Computers(tmp_path, CENTER)
    central = _central(client, computers, monkeypatch)
    await _reopened(client, central, project, topic)

    started = _start(
        client, central, SessionRef(project, topic, AGENT, harness="claude-code")
    )

    assert started.retired == TOKEN
    assert await _continues(client, topic) is None
    told = _told(client, topic)
    assert len(told) == 1
    assert told[0]["meta"]["i18n"]["content"]["key"] == "conversationNotCarriedReopened"


async def test_a_conversation_on_a_computer_that_is_away_starts_anew_at_once(
    client, room, tmp_path, bucket, monkeypatch
):
    """Back on the project's model after a member's own login ran it on their
    laptop, which is away now: no wait for it."""
    project, topic = room
    computers = Computers(tmp_path, CENTER, "laptop")
    computers.away.add("laptop")
    central = _central(client, computers, monkeypatch)
    async with client.test_factory() as db:
        room_row = await db.get(Topic, topic)
        resource = str(room_row.resource_id or room_row.id)
        await AgentSessionService(db).remember(
            conversation_id=topic,
            agent_handle=AGENT,
            resume_token=TOKEN,
            harness="claude-code",
            generation=0,
        )
        await remember_place(
            db,
            conversation_id=topic,
            agent_handle=AGENT,
            environment=None,
            runtime_location={
                "device_id": "laptop",
                "resource_id": resource,
                "channel": "owner",
                "runtime": {"agent_handle": AGENT, "state": "/somewhere"},
            },
            harness="claude-code",
        )
        await db.commit()

    started = _start(
        client, central, SessionRef(project, topic, AGENT, harness="claude-code")
    )

    assert started.device_id == CENTER
    assert started.retired == TOKEN
    told = _told(client, topic)
    assert [b["meta"]["i18n"]["content"]["key"] for b in told] == [
        "conversationNotCarriedOffline"
    ]


# --- whose private content it holds -------------------------------------------


async def _stamped(factory, conversation, session_id, *owners, private_text=()):
    """Records of session ``session_id`` in which the runner stamped a private
    memory call of each of ``owners`` and a private input block of each of
    ``private_text``."""
    stream = SimpleNamespace(
        conversation_id=conversation, agent_handle=AGENT, harness="claude-code"
    )
    rows = [
        {
            "sequence": n + 1,
            "at": datetime.now(UTC).isoformat(),
            "record": {
                "type": "assistant",
                "session_id": session_id,
                "message": {"role": "assistant", "content": []},
                "cheese": stamp,
            },
        }
        for n, stamp in enumerate(
            [{"private": {f"call-{owner}": owner}} for owner in owners]
            + [{"private_text": {"0": owner}} for owner in private_text]
        )
    ]
    await session_records.append(factory, stream, rows)


async def _laptop(client, owner) -> str:
    async with client.test_factory() as db:
        devices = sql_device_service(db)
        user_id = await registered(db, owner)
        code = await devices.start(f"{owner}-laptop")
        device = await devices.approve(
            code,
            owner_user_id=user_id,
            supply=Supply.self_hosted,
            team_id=await enrollment_team(devices, user_id),
        )
        await db.commit()
        return device.device_id


async def _may_carry(client, topic, target) -> bool:
    async with client.test_factory() as db:
        return await session_carry.may_carry(
            db,
            SessionRef(uuid.uuid4(), topic, AGENT, harness="claude-code"),
            TOKEN,
            target=target,
        )


async def test_a_conversation_holding_another_members_private_content_stays_off_a_members_computer(
    client, room, monkeypatch
):
    monkeypatch.setattr(settings, "agent_session_device_id", CENTER)
    _, topic = room
    alices = await _laptop(client, "alice")
    await _stamped(client.test_factory, topic, TOKEN, "alice", private_text=["bob"])

    assert await _may_carry(client, topic, alices) is False
    # The platform's session host is trusted with everyone's.
    assert await _may_carry(client, topic, CENTER) is True


async def test_a_conversation_holding_only_its_new_readers_private_content_may_go(
    client, room, monkeypatch
):
    monkeypatch.setattr(settings, "agent_session_device_id", CENTER)
    _, topic = room
    alices = await _laptop(client, "alice")
    await _stamped(client.test_factory, topic, TOKEN, "alice", private_text=["alice"])
    # Another conversation of the room held someone else's: not this one.
    await _stamped(client.test_factory, topic, "another-conversation", "bob")

    assert await _may_carry(client, topic, alices) is True


async def test_a_conversation_holding_only_its_owners_content_follows_to_their_laptop(
    client, tmp_path, bucket, monkeypatch
):
    """Bob's own Claude Code ran on the platform's computer, where only Bob's
    notes went into its conversation. It now runs on Bob's laptop, and the
    conversation goes there with it."""
    own = await _own_session(client, environment_on="alice")
    monkeypatch.setattr(settings, "agent_session_device_id", CENTER)
    computers = Computers(tmp_path, CENTER, own.bobs_laptop)
    async with client.test_factory() as db:
        sessions = AgentSessionService(db)
        await sessions.remember(
            conversation_id=own.topic_id,
            agent_handle=own.handle,
            resume_token=TOKEN,
            harness="claude-code",
            generation=0,
        )
        row = await sessions.ensure(own.topic_id, own.handle, harness="claude-code")
        resource = row.runtime_location["resource_id"]
        row.runtime_location = {
            "device_id": CENTER,
            "resource_id": resource,
            "channel": "device",
            "runtime": {"agent_handle": own.handle, "state": "/somewhere"},
        }
        await db.commit()
    await _stamped(client.test_factory, own.topic_id, TOKEN, private_text=["bob"])
    sent = _conversation(computers.projects(CENTER, own.project_id, resource))
    channel = OwnerChannel(
        DeviceChannel(hub=computers, session_factory=client.test_request_factory)
    )
    session = SessionRef(
        own.project_id, own.topic_id, own.handle, harness="claude-code"
    )

    started = _start(client, channel, session)

    assert started.device_id == own.bobs_laptop
    assert started.retired is None
    assert _held(computers.projects(own.bobs_laptop, own.project_id, resource)) == sent
