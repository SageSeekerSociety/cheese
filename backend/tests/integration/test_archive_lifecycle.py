"""Archival persists a configurable grace period without deleting resources."""

import asyncio
import os
import subprocess
import tarfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import ConflictError
from app.domain.machine.models import AiStatus, CloudHost, CloudHostHome, MachineStatus
from app.domain.project.services import ProjectService
from app.domain.topic import retire
from app.domain.topic.models import RoomCleanup
from app.domain.topic.services import TopicService
from tests.integration.conftest import registered, session_auth_headers

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


async def test_a_failed_check_after_the_stop_keeps_resources_and_retries_after_restart(
    client, monkeypatch
):
    room_id, cleanup_id = await archived_room(client, monkeypatch)
    entry = {"kind": "device", "device_id": "fixture", "resource_id": str(room_id)}
    inventory = AsyncMock(return_value=[entry])
    unpublished = [RuntimeError("the room has unpublished work")]

    async def step(device_id, project_id, resource_id, name, *rest):
        if name == "publication" and unpublished:
            raise unpublished.pop()

    action = AsyncMock(side_effect=step)
    monkeypatch.setattr(retire, "_inventory", inventory)
    monkeypatch.setattr(retire, "_device_action", action)
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
        assert operation.last_error == "the room has unpublished work"
    # A fresh worker/session resumes the same recorded resource, not a fresh inventory.
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
        await enrolled(session, "new-hands", "old-hands")
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


async def enrolled(session, *device_ids: str) -> None:
    """Give each machine a device record, as enrolling one does."""
    from app.domain.device.models import DeviceRow
    from app.domain.device.supply import Supply

    owner = await registered(session, "owner")
    for device_id in device_ids:
        session.add(
            DeviceRow(
                device_id=device_id,
                name=device_id,
                token=f"{device_id}-token",
                owner_user_id=owner,
                supply=Supply.cloud,
                created_at=datetime.now(UTC),
            )
        )
    await session.flush()


async def _room_with_lease(client, monkeypatch, lease: dict):
    from app.domain.agent_session.services import AgentSessionService

    room_id, cleanup_id = await archived_room(client, monkeypatch)
    async with client.test_factory() as session:
        conversation = await AgentSessionService(session).ensure(
            room_id, "worker", harness="claude-code"
        )
        conversation.work_lease = lease
        await session.commit()
    return cleanup_id


async def test_a_lease_on_a_removed_machine_does_not_hold_the_cleanup(
    client, monkeypatch
):
    resource = str(uuid.uuid4())
    cleanup_id = await _room_with_lease(
        client,
        monkeypatch,
        {"device_id": "removed", "resource_id": resource, "kind": "device"},
    )
    monkeypatch.setattr(retire.device_hub, "is_online", lambda device: False)
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        entries = await retire._inventory(session, operation, {})
        assert all(entry["device_id"] != "removed" for entry in entries)


async def test_a_lease_on_an_offline_machine_still_waits_for_it(client, monkeypatch):
    cleanup_id = await _room_with_lease(
        client,
        monkeypatch,
        {"device_id": "away", "resource_id": str(uuid.uuid4()), "kind": "device"},
    )
    monkeypatch.setattr(retire.device_hub, "is_online", lambda device: False)
    async with client.test_factory() as session:
        await enrolled(session, "away")
        operation = await session.get(RoomCleanup, cleanup_id)
        with pytest.raises(RuntimeError, match="offline"):
            await retire._inventory(session, operation, {})


async def test_a_lease_without_its_home_is_cleaned_by_the_rooms_inventory(
    client, monkeypatch
):
    cleanup_id = await _room_with_lease(
        client,
        monkeypatch,
        {"device_id": "center", "state": "/work/.runtime", "kind": "device"},
    )
    monkeypatch.setattr(retire.device_hub, "is_online", lambda device: True)
    async with client.test_factory() as session:
        await enrolled(session, "center")
        operation = await session.get(RoomCleanup, cleanup_id)
        home = str(operation.resource_id)
        inventory = {"center": [("home", str(operation.project_id), home)]}
        entries = await retire._inventory(session, operation, inventory)
        assert {(entry["device_id"], entry["resource_id"]) for entry in entries} == {
            ("center", home)
        }
        with pytest.raises(RuntimeError, match="inventory failed"):
            await retire._inventory(session, operation, {})


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
async def session_host_room(client, monkeypatch, tmp_path):
    """An archived room whose home, on the session host, holds a main session
    transcript and a subagent's."""
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
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
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
    client, session_host_room
):
    room = session_host_room
    assert _sweep(client) == {"completed": 1, "pending": 0}

    # The home is gone, its transcripts kept compressed beside the platform's
    # other files on the host.
    assert not room.home.exists()
    assert _archived_files(room.archive) == {
        "projects/-room/s1.jsonl": b'{"said":"main"}\n',
        "projects/-room/s1/subagents/agent-a.jsonl": b'{"said":"sub"}\n',
    }
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


async def test_a_process_still_in_a_claimed_room_is_waited_out_and_named(
    client, session_host_room, caplog
):
    """A process can still be inside a room after the cleanup claimed it. The
    sweep waits for it to leave: the room stays, the wait names the process,
    it is reported when it starts and not again every pass, and nothing reads
    it as a failure. Once the process is gone the room is cleaned up."""
    room = session_host_room
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, room.cleanup_id)
        operation.state = "claimed"
        operation.resources = [
            {
                "kind": "device",
                "device_id": "center",
                "resource_id": str(operation.resource_id),
            }
        ]
        await session.commit()
    holder = subprocess.Popen(["sleep", "300"], cwd=room.home)
    try:
        with caplog.at_level("DEBUG", logger="cheesex.topic.retire"):
            assert _sweep(client) == {"completed": 0, "pending": 1}
            assert _sweep(client) == {"completed": 0, "pending": 1}
        assert room.home.exists()
        async with client.test_factory() as session:
            operation = await session.get(RoomCleanup, room.cleanup_id)
            assert operation.state == "claimed"
            assert "sleep 300" in operation.last_error
        records = [r for r in caplog.records if r.name == "cheesex.topic.retire"]
        assert not [r for r in records if r.levelname == "ERROR"]
        assert len([r for r in records if r.levelname == "WARNING"]) == 1
    finally:
        holder.kill()
        holder.wait()
    assert _sweep(client) == {"completed": 1, "pending": 0}
    assert not room.home.exists()


async def test_a_rooms_cleanup_gives_back_its_homes_on_a_shared_cloud_host(
    client, monkeypatch, tmp_path
):
    """A cloud host carries other projects' sandboxes too. The room's cleanup
    removes the room's own directory there and the home that held the host
    for it; the host and everyone else's homes stay."""
    room_id, cleanup_id = await archived_room(client, monkeypatch)
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        project_id, resource_id = operation.project_id, str(operation.resource_id)
        other_project = await ProjectService(session).create(
            name="Other", owner_handle="owner"
        )
        other_room = await TopicService(session).create(
            project_id=other_project.id, title="Other room", created_by="owner"
        )
        host = CloudHost(
            machine_id=1,
            customer_id=7,
            account_id=9,
            offering_id=1,
            hostname="host-shared",
            login_user="cheese",
            cores=2,
            memory_mb=4096,
            disk_gb=20,
            status=MachineStatus.running,
            ai_status=AiStatus.disabled,
            device_id="host-dev",
        )
        session.add(host)
        await session.flush()
        for project, topic, resource in (
            (project_id, room_id, resource_id),
            (other_project.id, other_room.id, str(uuid.uuid4())),
        ):
            session.add(
                CloudHostHome(
                    host_id=host.id,
                    project_id=project,
                    topic_id=topic,
                    room_resource_id=resource,
                    resource_id=resource,
                    session_id=uuid.uuid4(),
                )
            )
        await session.commit()
        host_id = host.id
    machine = tmp_path / "host"
    home = machine / ".cheese/home" / str(project_id) / resource_id
    home.mkdir(parents=True)
    (home / "notes.txt").write_text("the room's own")
    monkeypatch.setattr(retire.device_hub, "exec", LocalDevice(machine).exec)
    monkeypatch.setattr(
        retire,
        "_inventory",
        AsyncMock(
            return_value=[
                {"kind": "device", "device_id": "host-dev", "resource_id": resource_id}
            ]
        ),
    )

    assert _sweep(client) == {"completed": 1, "pending": 0}

    assert not home.exists()
    async with client.test_factory() as session:
        homes = list(
            await session.scalars(
                select(CloudHostHome.topic_id).where(CloudHostHome.host_id == host_id)
            )
        )
        assert homes == [other_room.id]
        assert (await session.get(CloudHost, host_id)).released_at is None


@pytest.mark.parametrize("pushed", [True, False, None])
async def test_a_rooms_cleanup_deletes_an_archived_sandbox_home_only_once_pushed(
    client, monkeypatch, pushed
):
    """A session whose home was archived holds no directory on any machine:
    its host may be gone. The room's cleanup does not wait for that host. The
    archive goes with the room when its host found everything in it pushed;
    otherwise it is the only copy of that work, and the cleanup waits, still
    cancellable by unarchiving the room. An archive with no answer (written
    before hosts were asked) is not taken for pushed."""
    from app.domain.agent_session.services import AgentSessionService
    from app.domain.machine import lifecycle

    class Bucket:
        def __init__(self):
            self.objects = {"sandbox-archives/home.tar.gz": b"archived home"}

        async def delete(self, key):
            return self.objects.pop(key, None) is not None

    bucket = Bucket()
    monkeypatch.setattr(lifecycle, "private_storage", lambda: bucket)
    room_id, cleanup_id = await archived_room(client, monkeypatch)
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        project_id, generation = operation.project_id, str(operation.resource_id)
        resource = str(uuid.uuid4())
        conversation = await AgentSessionService(session).ensure(
            room_id, "worker", harness="claude-code"
        )
        # The lease still names the host the home was archived from.
        conversation.work_lease = {
            "device_id": "released-host",
            "resource_id": resource,
            "kind": "device",
        }
        session.add(
            CloudHostHome(
                host_id=None,
                project_id=project_id,
                topic_id=room_id,
                room_resource_id=generation,
                resource_id=resource,
                session_id=conversation.id,
                stopped_at=datetime.now(UTC),
                archive_key="sandbox-archives/home.tar.gz",
                archive_size=13,
                archive_md5="0" * 32,
                archive_published=pushed,
            )
        )
        await session.commit()

    if pushed:
        assert _sweep(client) == {"completed": 1, "pending": 0}
        assert bucket.objects == {}
        async with client.test_factory() as session:
            assert (
                await session.scalar(
                    select(CloudHostHome.id).where(CloudHostHome.topic_id == room_id)
                )
            ) is None
        return

    assert _sweep(client) == {"completed": 0, "pending": 1}
    assert _sweep(client) == {"completed": 0, "pending": 1}
    assert bucket.objects == {"sandbox-archives/home.tar.gz": b"archived home"}
    status = client.get(
        f"/topics/{room_id}/cleanup", headers=session_auth_headers("owner")
    ).json()["data"]
    assert status["state"] == "pending"
    assert "not pushed" in status["reason"]
    async with client.test_factory() as session:
        await TopicService(session).unarchive(room_id, by="owner")
        await session.commit()
    async with client.test_factory() as session:
        assert (await session.get(RoomCleanup, cleanup_id)).state == "cancelled"
        kept = await session.scalar(
            select(CloudHostHome).where(CloudHostHome.topic_id == room_id)
        )
        assert kept.archive_key == "sandbox-archives/home.tar.gz"
    assert bucket.objects == {"sandbox-archives/home.tar.gz": b"archived home"}


@pytest.mark.parametrize("pushed", [True, False])
async def test_a_room_that_became_a_task_is_still_cleaned_up(
    client, monkeypatch, pushed
):
    """A room archived before its cleanup finished became a closed task: its
    row is gone, its conversation stays. Its cleanup is not called off for
    that: the old generation's home goes once it is found pushed, and an
    unpushed one still holds the cleanup, as it would for a room."""
    from sqlalchemy import text

    from app.domain.agent_session.services import AgentSessionService
    from app.domain.machine import lifecycle

    class Bucket:
        def __init__(self):
            self.objects = {"sandbox-archives/home.tar.gz": b"archived home"}

        async def delete(self, key):
            return self.objects.pop(key, None) is not None

    bucket = Bucket()
    monkeypatch.setattr(lifecycle, "private_storage", lambda: bucket)
    room_id, cleanup_id = await archived_room(client, monkeypatch)
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        conversation = await AgentSessionService(session).ensure(
            room_id, "worker", harness="claude-code"
        )
        resource = str(uuid.uuid4())
        conversation.work_lease = {
            "device_id": "released-host",
            "resource_id": resource,
            "kind": "device",
        }
        session.add(
            CloudHostHome(
                host_id=None,
                project_id=operation.project_id,
                topic_id=room_id,
                room_resource_id=str(operation.resource_id),
                resource_id=resource,
                session_id=conversation.id,
                stopped_at=datetime.now(UTC),
                archive_key="sandbox-archives/home.tar.gz",
                archive_size=13,
                archive_md5="0" * 32,
                archive_published=pushed,
            )
        )
        await session.commit()
    # What the conversion does to the room: its row goes, its conversation
    # becomes the task's.
    async with client.test_factory() as session:
        await session.execute(
            text("ALTER TABLE topics DISABLE TRIGGER topics_conversation_unregistered")
        )
        await session.execute(
            text("DELETE FROM topics WHERE id = :id"), {"id": room_id}
        )
        await session.execute(
            text("ALTER TABLE topics ENABLE TRIGGER topics_conversation_unregistered")
        )
        await session.execute(
            text("UPDATE conversations SET kind = 'task' WHERE id = :id"),
            {"id": room_id},
        )
        await session.commit()

    if pushed:
        assert _sweep(client) == {"completed": 1, "pending": 0}
        assert bucket.objects == {}
        async with client.test_factory() as session:
            assert (await session.get(RoomCleanup, cleanup_id)).state == "complete"
            assert (
                await session.scalar(
                    select(CloudHostHome.id).where(CloudHostHome.topic_id == room_id)
                )
            ) is None
        return

    assert _sweep(client) == {"completed": 0, "pending": 1}
    assert bucket.objects == {"sandbox-archives/home.tar.gz": b"archived home"}
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        assert operation.state == "pending"
        assert "not pushed" in operation.last_error
