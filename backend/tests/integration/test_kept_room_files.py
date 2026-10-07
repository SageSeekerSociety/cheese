"""A room from before rooms had an executor keeps its files for thirty days.

Such a room worked in its directory with no repository behind it. Nothing of
it is deleted without its files going to the private bucket first; the
conversation is told until when and how to get them back, once; the copy goes
after thirty days; an operator lists and fetches it in between.

The machine here is a directory on this one: what the platform sends a device
(``resource_cleanup``, the storage listing) runs for real with ``HOME`` pointed
at it. The bucket is a small HTTP server speaking the part of S3 a single PUT,
HEAD, GET and DELETE use, reached through the platform's own S3 client.
"""

import asyncio
import hashlib
import io
import os
import subprocess
import sys
import tarfile
import threading
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import scripts.retained_files as retained_files
from app.core.config import settings
from app.core.storage import S3StorageBackend
from app.domain.agent import device_storage
from app.domain.agent_session.models import AgentSession
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.models import Block, BlockKind
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.machine.session_work import checkpoint_room
from app.domain.project.services import ProjectService
from app.domain.topic import retire
from app.domain.topic.models import KeptRoomFiles, RoomCleanup
from app.domain.topic.services import TopicService
from tests.integration.conftest import registered, session_auth_headers

pytestmark = pytest.mark.anyio


class Bucket:
    NAME = "private"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts = 0
        bucket = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _key(self):
                path = urllib.parse.urlsplit(self.path).path.lstrip("/")
                assert path.split("/", 1)[0] == Bucket.NAME, path
                return path.split("/", 1)[1]

            def _answer(self, status, body=b"", headers=()):
                self.send_response(status)
                for name, value in headers:
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def do_PUT(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                bucket.objects[self._key()] = body
                bucket.puts += 1
                self._answer(
                    200, headers=[("ETag", f'"{hashlib.md5(body).hexdigest()}"')]
                )

            def do_HEAD(self):
                body = bucket.objects.get(self._key())
                if body is None:
                    return self._answer(404)
                self._answer(
                    200,
                    body,
                    headers=[("ETag", f'"{hashlib.md5(body).hexdigest()}"')],
                )

            def do_GET(self):
                body = bucket.objects.get(self._key())
                self._answer(404) if body is None else self._answer(200, body)

            def do_DELETE(self):
                bucket.objects.pop(self._key(), None)
                self._answer(204)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.storage = S3StorageBackend(
            bucket=Bucket.NAME,
            endpoint_url=f"http://127.0.0.1:{self._server.server_address[1]}",
            access_key="test",
            secret_key="test",
        )

    def close(self):
        self._server.shutdown()


class Machine:
    """A self-hosted machine's connector: each command runs here, in the
    machine's own home directory."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.ran: list[str] = []

    def is_online(self, _device):
        return True

    def online_device_ids(self):
        return []

    def screens_for_topic(self, _topic):
        return []

    async def exec(self, device_id, argv, *, stdin=None, timeout=60, env=None, **_):
        self.ran.append(argv[2] if argv[:2] == ["python3", "-"] else argv[0])
        if argv[:2] == ["python3", "-"]:
            argv = [sys.executable, "-", *argv[2:]]
        done = await asyncio.to_thread(
            subprocess.run,
            argv,
            input=stdin,
            text=True,
            capture_output=True,
            # The bucket is on loopback, past any proxy the environment names.
            env={**os.environ, **(env or {}), "HOME": str(self.home), "no_proxy": "*"},
            timeout=timeout,
        )
        return {"exit": done.returncode, "stdout": done.stdout, "stderr": done.stderr}


@pytest.fixture
def bucket(monkeypatch):
    answering = Bucket()
    monkeypatch.setattr(device_storage, "private_storage", lambda: answering.storage)
    monkeypatch.setattr(retained_files, "private_storage", lambda: answering.storage)
    yield answering
    answering.close()


async def _old_room(client, tmp_path, *, files=True):
    """A room, a self-hosted machine of its project, and the room's home on it
    from before rooms had an executor, with files in its directory."""
    async with client.test_factory() as session:
        owner = await registered(session, "owner")
        project = await ProjectService(session).create(name="P", owner_handle="owner")
        room = await TopicService(session).create(
            project_id=project.id, title="Room", created_by="owner"
        )
        devices = sql_device_service(session)
        device = await devices.approve(
            await devices.start("old box"),
            owner_user_id=owner,
            supply=Supply.self_hosted,
        )
        device_id = device.device_id
        # The room's session worked on that machine once; its lease is gone.
        worker = await AgentSessionService(session).ensure(
            room.id, "worker", harness="claude-code"
        )
        worker.execution_request = {
            "retained_leases": [
                {"kind": "device", "device_id": device_id, "resource_id": str(room.id)}
            ]
        }
        await session.commit()
    machine = Machine(tmp_path / "machine")
    home = machine.home / ".cheese/home" / str(project.id) / str(room.id)
    (home / "room").mkdir(parents=True)
    if files:
        (home / "room/memory").mkdir()
        (home / "room/memory/notes.md").write_text("365 notes")
        (home / "room/deck.pptx").write_bytes(b"PK slides")
    return project.id, room.id, device_id, machine, home


def _keep(client, device_id, machine) -> int:
    """What a machine's connection runs, on the app's own loop."""
    return client.portal.call(
        lambda: device_storage.keep_device_room_files(
            client.test_request_factory, device_id, hub=machine
        )
    )


def _tar(body: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(body)) as bundle:
        return {
            member.name: bundle.extractfile(member).read()
            for member in bundle.getmembers()
            if member.isfile()
        }


async def _rows(client) -> list[KeptRoomFiles]:
    async with client.test_factory() as session:
        return list(await session.scalars(select(KeptRoomFiles)))


async def _notices(client, room_id) -> list[Block]:
    async with client.test_factory() as session:
        blocks = await session.scalars(
            select(Block).where(
                Block.conversation_id == room_id, Block.kind == BlockKind.event
            )
        )
        return [
            b for b in blocks if (b.meta or {}).get("event_type") == "room_files_kept"
        ]


async def test_a_machine_connecting_sends_an_old_rooms_files_and_tells_the_room_once(
    client, tmp_path, bucket
):
    project_id, room_id, device_id, machine, home = await _old_room(client, tmp_path)

    looked = _keep(client, device_id, machine)

    assert looked == 1
    key = f"kept-room-files/{project_id}/{room_id}.tar.gz"
    assert _tar(bucket.objects[key]) == {
        "room/memory/notes.md": b"365 notes",
        "room/deck.pptx": b"PK slides",
    }
    # Sent, not removed: the room may still be in use.
    assert (home / "room/deck.pptx").exists()
    [row] = await _rows(client)
    assert row.key == key and row.size == len(bucket.objects[key])
    assert row.room_id == room_id and row.task_id is None
    expires = datetime.now(UTC) + timedelta(days=30)
    assert abs(row.expires_at - expires) < timedelta(minutes=5)
    [told] = await _notices(client, room_id)
    assert expires.date().isoformat() in told.content
    assert "管理员" in told.content
    for word in ("机器", "电脑", "主机"):
        assert word not in told.content

    # The next connection looks at nothing it looked at before.
    machine.ran.clear()
    assert _keep(client, device_id, machine) == 0
    assert "keep-room" not in machine.ran
    assert bucket.puts == 1
    assert len(await _notices(client, room_id)) == 1


async def test_a_machine_no_session_worked_on_is_not_looked_at(
    client, tmp_path, bucket
):
    _project, room_id, device_id, machine, _home = await _old_room(client, tmp_path)
    async with client.test_factory() as session:
        for row in await session.scalars(select(AgentSession)):
            await session.delete(row)
        await session.commit()

    assert _keep(client, device_id, machine) == 0

    assert machine.ran == []
    assert bucket.objects == {}


async def test_a_home_with_nothing_off_the_forge_is_recorded_and_says_nothing(
    client, tmp_path, bucket
):
    _project, room_id, device_id, machine, _home = await _old_room(
        client, tmp_path, files=False
    )

    assert _keep(client, device_id, machine) == 1

    [row] = await _rows(client)
    assert row.key is None
    assert bucket.objects == {}
    assert await _notices(client, room_id) == []


async def _archive(client, monkeypatch, room_id, device_id, machine):
    monkeypatch.setattr(settings, "topic_archive_cleanup_delay_s", 0)
    async with client.test_factory() as session:
        await TopicService(session).archive(room_id, by="owner")
        await session.commit()
        cleanup_id = (await TopicService(session).get_or_404(room_id)).cleanup_id
    entry = {"kind": "device", "device_id": device_id, "resource_id": str(room_id)}
    monkeypatch.setattr(retire, "_inventory", AsyncMock(return_value=[entry]))
    monkeypatch.setattr(retire, "device_hub", machine)
    return cleanup_id


def _sweep(client):
    return client.portal.call(
        lambda: retire.sweep_retired_storage(
            client.test_request_factory, checkpoint=checkpoint_room
        )
    )


async def test_a_rooms_cleanup_sends_its_files_before_it_deletes_them(
    client, tmp_path, monkeypatch, bucket
):
    project_id, room_id, device_id, machine, home = await _old_room(client, tmp_path)
    # Work nowhere else holds no cleanup back any more.
    task = home / ".cheese/tasks" / str(uuid.uuid4())
    task.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(task)], check=True)
    (task / "draft.py").write_text("not committed")
    cleanup_id = await _archive(client, monkeypatch, room_id, device_id, machine)

    assert _sweep(client) == {"completed": 1, "pending": 0}

    assert not home.exists()
    key = f"kept-room-files/{project_id}/{room_id}.tar.gz"
    assert _tar(bucket.objects[key])["room/deck.pptx"] == b"PK slides"
    [row] = await _rows(client)
    assert row.key == key
    assert len(await _notices(client, room_id)) == 1
    async with client.test_factory() as session:
        assert (await session.get(RoomCleanup, cleanup_id)).state == "complete"


async def test_files_already_sent_are_not_sent_again_when_the_room_goes(
    client, tmp_path, monkeypatch, bucket
):
    _project, room_id, device_id, machine, home = await _old_room(client, tmp_path)
    _keep(client, device_id, machine)
    await _archive(client, monkeypatch, room_id, device_id, machine)

    assert _sweep(client) == {"completed": 1, "pending": 0}

    assert not home.exists()
    assert bucket.puts == 1
    assert len(await _rows(client)) == 1
    assert len(await _notices(client, room_id)) == 1


async def test_with_no_bucket_an_old_rooms_files_hold_its_cleanup(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(device_storage, "_private_bucket", lambda: None)
    _project, room_id, device_id, machine, home = await _old_room(client, tmp_path)
    cleanup_id = await _archive(client, monkeypatch, room_id, device_id, machine)

    assert _sweep(client) == {"completed": 0, "pending": 1}

    assert (home / "room/deck.pptx").read_bytes() == b"PK slides"
    async with client.test_factory() as session:
        assert "no bucket" in (await session.get(RoomCleanup, cleanup_id)).last_error


async def test_a_copy_goes_after_thirty_days_and_is_fetched_until_then(
    client, tmp_path, bucket, capsys, monkeypatch
):
    project_id, room_id, device_id, machine, _home = await _old_room(client, tmp_path)
    _keep(client, device_id, machine)
    key = f"kept-room-files/{project_id}/{room_id}.tar.gz"
    monkeypatch.setattr(
        retained_files, "async_session_factory", client.test_request_factory
    )

    assert client.portal.call(retained_files.list_kept) == 0
    listed = capsys.readouterr().out
    assert key in listed and str(room_id) in listed
    out = tmp_path / "fetched.tar.gz"
    assert client.portal.call(retained_files.download, key, out) == 0
    assert out.read_bytes() == bucket.objects[key]
    assert (
        client.portal.call(retained_files.download, "kept-room-files/other.tar.gz", out)
        == 1
    )

    async with client.test_factory() as session:
        row = await session.scalar(select(KeptRoomFiles))
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
    _sweep(client)

    assert key not in bucket.objects
    [row] = await _rows(client)
    assert row.deleted_at is not None
    capsys.readouterr()
    client.portal.call(retained_files.list_kept)
    assert key not in capsys.readouterr().out


async def test_a_task_that_was_a_room_is_told_in_its_own_conversation(
    client, tmp_path, bucket
):
    """Most such rooms on dev became tasks of a channel; the task's
    conversation is the one that held the files."""
    project_id, room_id, device_id, machine, _home = await _old_room(
        client, tmp_path, files=False
    )
    task_id = uuid.UUID(
        client.post(
            f"/topics/{room_id}/tasks",
            json={"title": "Old room"},
            headers=session_auth_headers("owner"),
        ).json()["data"]["id"]
    )
    home = machine.home / ".cheese/home" / str(project_id) / str(task_id)
    (home / "room").mkdir(parents=True)
    (home / "room/video.mp4").write_bytes(b"\x00video")

    _keep(client, device_id, machine)

    [row] = [r for r in await _rows(client) if r.key is not None]
    assert row.room_id == room_id and row.task_id == task_id
    async with client.test_factory() as session:
        [told] = [
            b
            for b in await session.scalars(
                select(Block).where(Block.kind == BlockKind.event)
            )
            if (b.meta or {}).get("event_type") == "room_files_kept"
        ]
    assert told.conversation_id == task_id
