"""Archival persists a configurable grace period without deleting resources."""

import os
import subprocess
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.errors import ConflictError
from app.domain.project.services import ProjectService
from app.domain.topic import retire
from app.domain.topic.models import RoomCleanup
from app.domain.topic.services import TopicService

pytestmark = pytest.mark.anyio


async def test_archive_deadline_is_stable_across_retries_and_configuration_changes(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "topic_archive_cleanup_delay_s", 37)
    async with client.test_factory() as session:
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
    async with client.test_factory() as session:
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
    assert await retire.sweep_retired_storage(client.test_factory) == {
        "completed": 0,
        "pending": 0,
    }
    action.assert_not_awaited()


async def test_failed_confirmation_keeps_resources_and_retries_after_restart(
    client, monkeypatch
):
    room_id, cleanup_id = await archived_room(client, monkeypatch)
    entry = {"kind": "device", "device_id": "fixture", "resource_id": str(room_id)}
    inventory = AsyncMock(return_value=[entry])
    action = AsyncMock()
    flush = AsyncMock(side_effect=RuntimeError("object store offline"))
    monkeypatch.setattr(retire, "_inventory", inventory)
    monkeypatch.setattr(retire, "_device_action", action)
    monkeypatch.setattr(retire, "_flush_transcripts", flush)
    result = await retire.sweep_retired_storage(client.test_factory)
    assert result == {"completed": 0, "pending": 1}
    assert [call.args[3] for call in action.await_args_list] == [
        "prepare",
        "publication",
    ]
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        assert operation.state == "pending"
        assert operation.last_error == "object store offline"
    # A fresh worker/session resumes the same recorded resource, not a fresh inventory.
    flush.side_effect = None
    flush.return_value = []
    assert await retire.sweep_retired_storage(client.test_factory) == {
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
    await retire.sweep_retired_storage(client.test_factory)
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
    await retire.sweep_retired_storage(client.test_factory)
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        assert operation.state == "pending"
        assert "unpublished" in operation.last_error
        await TopicService(session).unarchive(room_id, by="owner")
        await session.commit()
    assert not target.exists()
    assert (work / "unfinished.py").read_text() == "unpublished source"


async def test_cloud_inventory_cannot_discard_unrecognized_transcripts(
    client, monkeypatch
):
    room_id, cleanup_id = await archived_room(client, monkeypatch)
    machine = SimpleNamespace(id=uuid.uuid4(), device_id="cloud")
    monkeypatch.setattr(
        retire.MachineService, "topic_machine", AsyncMock(return_value=machine)
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
        retire.ws._point_at_the_store_relatively(repo, work)
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
    monkeypatch.setattr(retire, "_flush_transcripts", AsyncMock(return_value=[]))
    await retire.sweep_retired_storage(client.test_factory)
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
                "transcripts": [],
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
    monkeypatch.setattr(retire, "_flush_transcripts", AsyncMock(return_value=[]))
    await retire.sweep_retired_storage(client.test_factory)
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        assert operation.state == "claimed" and operation.last_error == "device offline"
        room = await TopicService(session).unarchive(room_id, by="owner")
        assert isinstance(room.resource_id, uuid.UUID) and room.resource_id != room_id
        await session.commit()
    action.side_effect = None
    assert await retire.sweep_retired_storage(client.test_factory) == {
        "completed": 1,
        "pending": 0,
    }
    assert action.await_args.args[2] == str(room_id)
    assert (new_directory / "new.py").read_text() == "new work"


async def test_a_sweep_asked_for_while_one_runs_makes_it_go_round_again(
    client, monkeypatch
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
    # one that runs.
    for _ in range(100):
        if not retire._sweeping:
            break
        await asyncio.sleep(0.05)
    assert not retire._sweeping
    monkeypatch.setattr(retire, "_sweep_once", slow_pass)
    first = asyncio.create_task(retire.sweep_retired_storage(client.test_factory))
    await asyncio.wait_for(started.wait(), timeout=5)
    # Two more asks while the first is still running: neither starts a sweep.
    assert await retire.sweep_retired_storage(client.test_factory) == {
        "completed": 0,
        "pending": 0,
    }
    assert await retire.sweep_retired_storage(client.test_factory) == {
        "completed": 0,
        "pending": 0,
    }
    assert passes == 1
    release.set()
    await asyncio.wait_for(first, timeout=5)
    assert passes == 2


async def test_a_cleanup_leased_to_another_sweep_is_left_alone_until_it_expires(
    client, monkeypatch
):
    from datetime import UTC, datetime

    room_id, cleanup_id = await archived_room(client, monkeypatch)
    entry = {"kind": "device", "device_id": "fixture", "resource_id": str(room_id)}
    monkeypatch.setattr(retire, "_inventory", AsyncMock(return_value=[entry]))
    monkeypatch.setattr(retire, "_device_action", AsyncMock())
    monkeypatch.setattr(retire, "_flush_transcripts", AsyncMock(return_value=[]))
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        operation.lease_until = datetime.now(UTC) + timedelta(minutes=10)
        operation.lease_holder = "another-backend:1:deadbeef"
        await session.commit()
    # Another process is on it: this sweep does not touch it.
    assert await retire.sweep_retired_storage(client.test_factory) == {
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
    assert await retire.sweep_retired_storage(client.test_factory) == {
        "completed": 1,
        "pending": 0,
    }
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        assert operation.state == "complete"
        assert operation.lease_until is None and operation.lease_holder is None
