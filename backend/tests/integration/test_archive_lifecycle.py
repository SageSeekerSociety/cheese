"""Archival persists a configurable grace period without deleting resources."""

import asyncio
import json
import os
import subprocess
import tarfile
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.errors import ConflictError
from app.domain.project.services import ProjectService
from app.domain.topic import retire
from app.domain.topic.models import RoomCleanup
from app.domain.topic.services import TopicService
from tests.integration.conftest import registered

pytestmark = pytest.mark.anyio


async def test_archive_deadline_is_stable_across_retries_and_configuration_changes(
    business_db_factory, monkeypatch
):
    monkeypatch.setattr(settings, "topic_archive_cleanup_delay_s", 37)
    async with business_db_factory() as session:
        await registered(session, "owner")
        project = await ProjectService(session).create(name="P", owner_handle="owner")
        service = TopicService(session)
        room = await service.create(
            project_id=project.id, title="Room", created_by="owner"
        )
        await service.archive(room.id, by="owner")
        assert room.archived_at is not None
        deadline = room.archived_at + timedelta(seconds=37)
        assert room.cleanup_due_at == deadline
        await session.commit()
        room_id = room.id
    monkeypatch.setattr(settings, "topic_archive_cleanup_delay_s", 900)
    async with business_db_factory() as session:
        service = TopicService(session)
        room = await service.archive(room_id, by="owner")
        assert room.cleanup_due_at == deadline
        await service.unarchive(room_id, by="owner")
        assert room.cleanup_due_at is None
        await service.archive(room_id, by="owner")
        assert room.cleanup_due_at == room.archived_at + timedelta(seconds=900)


async def archived_room(client, monkeypatch):
    monkeypatch.setattr(settings, "topic_archive_cleanup_delay_s", 0)
    async with client.test_factory() as session:
        await registered(session, "owner")
        project = await ProjectService(session).create(name="P", owner_handle="owner")
        room = await TopicService(session).create(
            project_id=project.id, title="Room", created_by="owner"
        )
        await TopicService(session).archive(room.id, by="owner")
        await session.commit()
        return room.id, room.cleanup_id


async def test_cancel_before_claim_reuses_environment_without_device_commands(
    client, monkeypatch
):
    room_id, cleanup_id = await archived_room(client, monkeypatch)
    action = AsyncMock()
    monkeypatch.setattr(retire, "_device_action", action)
    async with client.test_factory() as session:
        room = await TopicService(session).unarchive(room_id, by="owner")
        assert room.resource_id is None
        assert (await session.get(RoomCleanup, cleanup_id)).state == "cancelled"
        await session.commit()
    assert client.portal.call(
        lambda: retire.sweep_retired_storage(client.test_request_factory)
    ) == {
        "completed": 0,
        "pending": 0,
    }
    action.assert_not_awaited()


async def test_failed_event_delivery_keeps_resources_and_retries_after_restart(
    client, monkeypatch
):
    room_id, cleanup_id = await archived_room(client, monkeypatch)
    entry = {"kind": "device", "device_id": "fixture", "resource_id": str(room_id)}
    inventory = AsyncMock(return_value=[entry])
    action = AsyncMock()
    deliver = AsyncMock(side_effect=RuntimeError("final hook event delivery failed"))
    monkeypatch.setattr(retire, "_inventory", inventory)
    monkeypatch.setattr(retire, "_device_action", action)
    monkeypatch.setattr(retire, "_deliver_events", deliver)
    result = client.portal.call(
        lambda: retire.sweep_retired_storage(client.test_request_factory)
    )
    assert result == {"completed": 0, "pending": 1}
    assert [call.args[3] for call in action.await_args_list] == [
        "prepare",
        "publication",
    ]
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        assert operation.state == "pending"
        assert operation.last_error == "final hook event delivery failed"
    # A fresh worker/session resumes the same recorded resource, not a fresh inventory.
    deliver.side_effect = None
    assert client.portal.call(
        lambda: retire.sweep_retired_storage(client.test_request_factory)
    ) == {
        "completed": 1,
        "pending": 0,
    }
    assert inventory.await_count == 1
    assert action.await_args.args[3] == "remove"


async def test_uncertain_stop_blocks_reuse_until_device_confirms(client, monkeypatch):
    room_id, cleanup_id = await archived_room(client, monkeypatch)
    monkeypatch.setattr(
        retire,
        "_inventory",
        AsyncMock(
            return_value=[
                {
                    "kind": "device",
                    "device_id": "fixture",
                    "resource_id": str(room_id),
                }
            ]
        ),
    )
    monkeypatch.setattr(
        retire, "_device_action", AsyncMock(side_effect=TimeoutError("no stop receipt"))
    )
    client.portal.call(
        lambda: retire.sweep_retired_storage(client.test_request_factory)
    )
    async with client.test_factory() as session:
        assert (await session.get(RoomCleanup, cleanup_id)).state == "preparing"
        with pytest.raises(ConflictError, match="正在停止"):
            await TopicService(session).unarchive(room_id, by="owner")


async def test_unpublished_backend_source_can_be_reopened_before_parking(
    client, monkeypatch, tmp_path
):
    room_id, cleanup_id = await archived_room(client, monkeypatch)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    (work / "unfinished.py").write_text("unpublished source")
    target = tmp_path / "retired"
    monkeypatch.setattr(
        retire,
        "_inventory",
        AsyncMock(
            return_value=[
                {"kind": "worktree", "path": str(work), "retired": str(target)}
            ]
        ),
    )
    client.portal.call(
        lambda: retire.sweep_retired_storage(client.test_request_factory)
    )
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        assert operation.state == "pending"
        assert "unpublished" in operation.last_error
        await TopicService(session).unarchive(room_id, by="owner")
        await session.commit()
    assert not target.exists()
    assert (work / "unfinished.py").read_text() == "unpublished source"


async def test_cloud_inventory_cannot_discard_unrecognized_directories(
    client, monkeypatch
):
    room_id, cleanup_id = await archived_room(client, monkeypatch)

    machine = SimpleNamespace(id=uuid.uuid4(), device_id="cloud")
    monkeypatch.setattr(
        retire.MachineService,
        "list_active_for_topic",
        AsyncMock(return_value=[machine]),
    )
    monkeypatch.setattr(
        retire,
        "sql_device_service",
        lambda _: SimpleNamespace(
            topic_binding=AsyncMock(return_value=None),
            list_topic_bindings=AsyncMock(return_value=[]),
        ),
    )
    monkeypatch.setattr(retire.device_hub, "is_online", lambda _: True)
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        inventory = {
            "cloud": [
                ("home", str(operation.project_id), str(room_id)),
                ("home", str(operation.project_id), str(uuid.uuid4())),
            ]
        }
        with pytest.raises(RuntimeError, match="unrecognized"):
            await retire._inventory(session, operation, inventory)


@pytest.mark.parametrize("pointer", ["absolute", "relative", "moved-relative"])
async def test_parked_worktree_frees_the_branch_before_old_device_is_removed(
    client, monkeypatch, tmp_path, pointer
):
    room_id, cleanup_id = await archived_room(client, monkeypatch)
    repo, work = tmp_path / "repo", tmp_path / "work"
    parked = tmp_path / "retired" / "operation" / "work"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "code.py").write_text("published code")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "worktree", "add", "-qb", "room", str(work)], cwd=repo, check=True
    )
    monkeypatch.setattr(retire.ws, "_repo", lambda _: repo)
    if pointer != "absolute":
        admin = repo / ".git" / "worktrees" / "work"
        (work / ".git").write_text(f"gitdir: {os.path.relpath(admin, work)}\n")
    if pointer == "moved-relative":
        parked.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "worktree", "move", str(work), str(parked)], cwd=repo, check=True
        )
        admin = repo / ".git" / "worktrees" / "work"
        # Reproduce a move interrupted with the old relative pointer intact.
        (parked / ".git").write_text(f"gitdir: {os.path.relpath(admin, work)}\n")
    monkeypatch.setattr(
        retire,
        "_inventory",
        AsyncMock(
            return_value=[
                {"kind": "device", "device_id": "fixture", "resource_id": str(room_id)},
                {"kind": "worktree", "path": str(work), "retired": str(parked)},
            ]
        ),
    )

    async def old_device(*args):
        if args[3] == "remove":
            raise RuntimeError("old device offline")

    monkeypatch.setattr(retire, "_device_action", old_device)
    monkeypatch.setattr(retire, "_deliver_events", AsyncMock())
    client.portal.call(
        lambda: retire.sweep_retired_storage(client.test_request_factory)
    )
    async with client.test_factory() as session:
        assert (await session.get(RoomCleanup, cleanup_id)).state == "claimed"
        await TopicService(session).unarchive(room_id, by="owner")
        await session.commit()
    subprocess.run(
        ["git", "worktree", "add", "-q", str(work), "room"], cwd=repo, check=True
    )
    assert (work / "code.py").read_text() == "published code"
    assert (parked / "code.py").read_text() == "published code"


async def test_reopen_after_claim_never_redirects_old_deletion(
    client, monkeypatch, tmp_path
):
    room_id, cleanup_id = await archived_room(client, monkeypatch)
    old_directory = tmp_path / "old-retired"
    new_directory = tmp_path / "worktree"
    new_directory.mkdir()
    (new_directory / "new.py").write_text("new work")
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        operation.state = "claimed"
        operation.resources = [
            {
                "kind": "device",
                "device_id": "fixture",
                "resource_id": str(room_id),
            },
            # Deletion succeeded before a crash; the old pathname now holds new work.
            {
                "kind": "worktree",
                "path": str(new_directory),
                "retired": str(old_directory),
            },
        ]
        await session.commit()
    action = AsyncMock(side_effect=RuntimeError("device offline"))
    monkeypatch.setattr(retire, "_device_action", action)
    monkeypatch.setattr(retire, "_deliver_events", AsyncMock())
    client.portal.call(
        lambda: retire.sweep_retired_storage(client.test_request_factory)
    )
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        assert operation.state == "claimed" and operation.last_error == "device offline"
        room = await TopicService(session).unarchive(room_id, by="owner")
        assert isinstance(room.resource_id, uuid.UUID) and room.resource_id != room_id
        await session.commit()
    action.side_effect = None
    assert client.portal.call(
        lambda: retire.sweep_retired_storage(client.test_request_factory)
    ) == {
        "completed": 1,
        "pending": 0,
    }
    assert action.await_args.args[2] == str(room_id)
    assert (new_directory / "new.py").read_text() == "new work"


async def test_a_sweep_asked_for_while_one_runs_makes_it_go_round_again(
    business_db_factory, monkeypatch
):
    """Every device reconnect asks for a sweep; after a restart that is dozens
    at once. Only one runs, and the asks are not lost: the running sweep goes
    round again when it finishes."""
    import asyncio

    started = asyncio.Event()
    release = asyncio.Event()
    passes = 0

    async def slow_pass(sessions):
        nonlocal passes
        passes += 1
        if passes == 1:
            started.set()
            await release.wait()
        return {"completed": 0, "pending": 0}

    # The app's startup sweep may still be running; this test wants to be the
    # sweep that runs, not one more caller waiting on that one.
    for _ in range(100):
        if not retire._sweeping:
            break
        await asyncio.sleep(0.05)
    assert not retire._sweeping
    monkeypatch.setattr(retire, "_sweep_once", slow_pass)
    first = asyncio.create_task(retire.sweep_retired_storage(business_db_factory))
    await asyncio.wait_for(started.wait(), timeout=5)
    # Two more asks while the first is still running: neither starts a sweep.
    waiting = [
        asyncio.create_task(retire.sweep_retired_storage(business_db_factory))
        for _ in range(2)
    ]
    await asyncio.sleep(0.05)
    assert passes == 1, "a second sweep started while one was running"
    assert not any(task.done() for task in waiting), "a waiter answered early"
    release.set()
    await asyncio.wait_for(first, timeout=5)
    assert passes == 2
    # The waiters answer for the round that covered them, not with zeros.
    assert await asyncio.wait_for(asyncio.gather(*waiting), timeout=5) == [
        {"completed": 0, "pending": 0},
        {"completed": 0, "pending": 0},
    ]


async def test_a_cleanup_leased_to_another_sweep_is_left_alone_until_it_expires(
    client, monkeypatch
):
    from datetime import UTC, datetime

    room_id, cleanup_id = await archived_room(client, monkeypatch)
    entry = {"kind": "device", "device_id": "fixture", "resource_id": str(room_id)}
    monkeypatch.setattr(retire, "_inventory", AsyncMock(return_value=[entry]))
    monkeypatch.setattr(retire, "_device_action", AsyncMock())
    monkeypatch.setattr(retire, "_deliver_events", AsyncMock())
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        operation.lease_until = datetime.now(UTC) + timedelta(minutes=10)
        operation.lease_holder = "another-backend:1:deadbeef"
        await session.commit()
    # Another process is on it: this sweep does not touch it.
    assert client.portal.call(
        lambda: retire.sweep_retired_storage(client.test_request_factory)
    ) == {
        "completed": 0,
        "pending": 0,
    }
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        assert operation.state == "pending"
        assert operation.lease_holder == "another-backend:1:deadbeef"
        # That process died: its lease ran out.
        operation.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert client.portal.call(
        lambda: retire.sweep_retired_storage(client.test_request_factory)
    ) == {
        "completed": 1,
        "pending": 0,
    }
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        assert operation.state == "complete"
        assert operation.lease_until is None and operation.lease_holder is None


async def test_inventory_retains_every_session_work_allocation(client, monkeypatch):
    from app.domain.agent_session.services import AgentSessionService

    room_id, cleanup_id = await archived_room(client, monkeypatch)
    current, retained = str(uuid.uuid4()), str(uuid.uuid4())
    monkeypatch.setattr(retire.device_hub, "is_online", lambda _: True)
    async with client.test_factory() as session:
        conversation = await AgentSessionService(session).ensure(
            room_id, "worker", harness="claude-code"
        )
        conversation.work_lease = {
            "device_id": "new-hands",
            "resource_id": current,
            "kind": "device",
        }
        conversation.execution_request = {
            "retained_leases": [
                {"device_id": "old-hands", "resource_id": retained, "kind": "device"}
            ]
        }
        await session.commit()
        operation = await session.get(RoomCleanup, cleanup_id)
        entries = await retire._inventory(
            session, operation, {"new-hands": [], "old-hands": []}
        )
        assert {(entry["device_id"], entry["resource_id"]) for entry in entries} == {
            ("new-hands", current),
            ("old-hands", retained),
        }
        with pytest.raises(RuntimeError, match="inventory failed"):
            await retire._inventory(session, operation, {"new-hands": []})


class LocalDevice:
    """A device whose commands run on this machine, with HOME at a directory of
    the test's, so cleanup runs the real device-side script."""

    def __init__(self, home: Path) -> None:
        self.home = home

    async def exec(
        self, device_id, argv, *, cwd=None, env=None, timeout=60, stdin=None
    ):
        result = await asyncio.to_thread(
            subprocess.run,
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            env={**os.environ, **(env or {}), "HOME": str(self.home)},
            timeout=timeout,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit": result.returncode,
            "truncated": False,
        }


@pytest.fixture
def platform_requests():
    """Every request a device sends to the platform, answered as accepted."""
    seen: list[str] = []

    class Accept(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._accept()

        do_PUT = do_POST

        def _accept(self):
            seen.append(f"{self.command} {self.path}")
            body = json.dumps({"code": 200, "data": {}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Accept)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
async def session_host_room(client, monkeypatch, tmp_path, platform_requests):
    """An archived room whose home, on the session host, holds a main session
    transcript, a subagent's, and one hook event its sender had not sent yet."""
    base, _seen = platform_requests
    room_id, cleanup_id = await archived_room(client, monkeypatch)
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        project_id, resource_id = operation.project_id, operation.resource_id
    machine = tmp_path / "session-host"
    home = machine / ".cheese/home" / str(project_id) / str(resource_id)
    sessions = home / ".claude/projects/-room"
    (sessions / "s1/subagents").mkdir(parents=True)
    (sessions / "s1.jsonl").write_bytes(b'{"said":"main"}\n')
    (sessions / "s1/subagents/agent-a.jsonl").write_bytes(b'{"said":"sub"}\n')
    (home / ".cheese/cheese-spool").mkdir(parents=True)
    (home / ".cheese/cheese-spool/0001").write_text('{"hook_event_name":"Stop"}')
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    monkeypatch.setattr(settings, "connector_public_base", base)
    monkeypatch.setattr(retire.device_hub, "exec", LocalDevice(machine).exec)
    entry = {
        "kind": "device",
        "device_id": "center",
        "resource_id": str(resource_id),
    }
    monkeypatch.setattr(retire, "_inventory", AsyncMock(return_value=[entry]))
    archive = (
        machine
        / ".cheese/transcripts"
        / str(project_id)
        / str(room_id)
        / f"{resource_id}.tar.gz"
    )
    return SimpleNamespace(
        room_id=room_id, cleanup_id=cleanup_id, home=home, archive=archive
    )


def _sweep(client) -> dict:
    return client.portal.call(
        lambda: retire.sweep_retired_storage(client.test_request_factory)
    )


def _archived_files(archive: Path) -> dict[str, bytes]:
    with tarfile.open(archive) as bundle:
        return {
            member.name: bundle.extractfile(member).read()
            for member in bundle.getmembers()
            if member.isfile()
        }


async def _make_due(client, cleanup_id) -> None:
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        operation.due_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()


async def test_cleanup_keeps_transcripts_on_the_session_host_for_thirty_days(
    client, session_host_room, platform_requests
):
    room = session_host_room
    _base, seen = platform_requests
    assert _sweep(client) == {"completed": 1, "pending": 0}

    # The home is gone, its transcripts kept compressed beside the platform's
    # other files on the host, and the only thing sent anywhere was the event.
    assert not room.home.exists()
    assert _archived_files(room.archive) == {
        "projects/-room/s1.jsonl": b'{"said":"main"}\n',
        "projects/-room/s1/subagents/agent-a.jsonl": b'{"said":"sub"}\n',
    }
    assert seen == [f"POST /sandbox/hooks/{room.room_id}"]
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, room.cleanup_id)
        assert operation.state == "retained"
        kept_for = operation.due_at - datetime.now(UTC)
        assert timedelta(days=29, hours=23) < kept_for <= timedelta(days=30)

    # Not due yet: a sweep leaves them alone.
    assert _sweep(client) == {"completed": 0, "pending": 0}
    assert room.archive.exists()

    await _make_due(client, room.cleanup_id)
    assert _sweep(client) == {"completed": 1, "pending": 0}
    assert not room.archive.exists()
    async with client.test_factory() as session:
        assert (await session.get(RoomCleanup, room.cleanup_id)).state == "complete"
    assert seen == [f"POST /sandbox/hooks/{room.room_id}"]


async def test_a_room_reopened_while_its_transcripts_are_kept_lets_them_expire(
    client, session_host_room
):
    room = session_host_room
    _sweep(client)
    async with client.test_factory() as session:
        reopened = await TopicService(session).unarchive(room.room_id, by="owner")
        assert reopened.resource_id not in {None, room.room_id}
        await session.commit()
        # Nothing is restored into the new generation; the kept copy stays put.
        assert (await session.get(RoomCleanup, room.cleanup_id)).state == "retained"
    assert room.archive.exists()
    await _make_due(client, room.cleanup_id)
    assert _sweep(client) == {"completed": 1, "pending": 0}
    assert not room.archive.exists()


async def test_a_device_other_than_the_session_host_keeps_no_transcripts(
    client, session_host_room, monkeypatch
):
    room = session_host_room
    monkeypatch.setattr(settings, "agent_session_device_id", "some-other-host")
    assert _sweep(client) == {"completed": 1, "pending": 0}
    assert not room.home.exists()
    assert not room.archive.exists()
    async with client.test_factory() as session:
        assert (await session.get(RoomCleanup, room.cleanup_id)).state == "complete"
