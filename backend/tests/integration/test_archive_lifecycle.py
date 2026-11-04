"""Archival persists a configurable grace period without deleting resources."""

import asyncio
import hashlib
import http.server
import os
import subprocess
import tarfile
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.device_failures import DeviceOffline
from app.core.errors import ConflictError
from app.domain.agent import device_storage
from app.domain.agent.models import AgentTurn
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.adapters import platform_line
from app.domain.delivery.ledger import settle_card
from app.domain.device.wiring import sql_device_service
from app.domain.machine.models import AiStatus, CloudHost, MachineStatus, SandboxHome
from app.domain.machine.session_work import checkpoint_room
from app.domain.review.archive import close_cards_for_archived_topic
from app.domain.room_cleanup import retire
from app.domain.room_task.archive import close_room_tasks
from app.domain.topic.models import RoomCleanup
from app.domain.topic.services import TopicService
from app.domain.user.tokens import digest_token
from tests.integration.conftest import project_service, registered
from tests.support.devices import enrollment_team

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _retry_at_once(monkeypatch, request):
    """These are about what one attempt does, so a held cleanup is due again at
    once. How long it waits is its own test below."""
    if "backs_off" not in request.node.name:
        monkeypatch.setattr(retire, "RETRY_FIRST", timedelta(0))
        monkeypatch.setattr(retire, "RETRY_MOST", timedelta(0))


async def test_archive_deadline_is_stable_across_retries_and_configuration_changes(
    business_db_factory, monkeypatch
):
    monkeypatch.setattr(settings, "topic_archive_cleanup_delay_s", 37)
    async with business_db_factory() as session:
        await registered(session, "owner")
        project = await project_service(session).create(name="P", owner_handle="owner")
        service = TopicService(session)
        room = await service.create(
            project_id=project.id, title="Room", created_by="owner"
        )
        await service.archive(
            room.id,
            by="owner",
            settle_card=settle_card,
            cards=close_cards_for_archived_topic,
            close_tasks=close_room_tasks,
            events=platform_line,
        )
        assert room.archived_at is not None
        deadline = room.archived_at + timedelta(seconds=37)
        assert room.cleanup_due_at == deadline
        await session.commit()
        room_id = room.id
    monkeypatch.setattr(settings, "topic_archive_cleanup_delay_s", 900)
    async with business_db_factory() as session:
        service = TopicService(session)
        room = await service.archive(
            room_id,
            by="owner",
            settle_card=settle_card,
            cards=close_cards_for_archived_topic,
            close_tasks=close_room_tasks,
            events=platform_line,
        )
        assert room.cleanup_due_at == deadline
        await service.unarchive(
            room_id,
            by="owner",
            sessions=AgentSessionService(session),
            events=platform_line,
        )
        assert room.cleanup_due_at is None
        # Unarchiving and archiving again are two requests: two transactions.
        await session.commit()
        await service.archive(
            room_id,
            by="owner",
            settle_card=settle_card,
            cards=close_cards_for_archived_topic,
            close_tasks=close_room_tasks,
            events=platform_line,
        )
        assert room.cleanup_due_at == room.archived_at + timedelta(seconds=900)


# The machines these tests clean up on are enrolled ones, as every machine a
# room ran on is until it is removed; a machine with no record is gone, and the
# cleanup skips it ("removed", "released-host" and "away" are set up per test).
KNOWN_MACHINES = ("fixture", "center", "host-dev", "some-other-host")


async def archived_room(client, monkeypatch):
    monkeypatch.setattr(settings, "topic_archive_cleanup_delay_s", 0)
    async with client.test_factory() as session:
        await registered(session, "owner")
        await enrolled(session, *KNOWN_MACHINES)
        project = await project_service(session).create(name="P", owner_handle="owner")
        room = await TopicService(session).create(
            project_id=project.id, title="Room", created_by="owner"
        )
        await TopicService(session).archive(
            room.id,
            by="owner",
            settle_card=settle_card,
            cards=close_cards_for_archived_topic,
            close_tasks=close_room_tasks,
            events=platform_line,
        )
        await session.commit()
        return room.id, room.cleanup_id


async def test_cancel_before_claim_reuses_environment_without_device_commands(
    client, monkeypatch
):
    room_id, cleanup_id = await archived_room(client, monkeypatch)
    action = AsyncMock()
    monkeypatch.setattr(retire, "_device_action", action)
    async with client.test_factory() as session:
        room = await TopicService(session).unarchive(
            room_id,
            by="owner",
            sessions=AgentSessionService(session),
            events=platform_line,
        )
        assert room.resource_id is None
        assert (await session.get(RoomCleanup, cleanup_id)).state == "cancelled"
        await session.commit()
    assert client.portal.call(
        lambda: retire.sweep_retired_storage(
            client.test_request_factory, checkpoint=checkpoint_room
        )
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
    action = AsyncMock(return_value={"ready": True})
    monkeypatch.setattr(retire, "_inventory", inventory)
    monkeypatch.setattr(retire, "_device_action", action)
    # A turn still persisting its result after the room was stopped.
    turn = AgentTurn(
        id=uuid.uuid4(),
        continuation_id=uuid.uuid4(),
        conversation_id=room_id,
        author="owner",
        started_at=datetime.now(UTC),
    )
    async with client.test_factory() as session:
        session.add(turn)
        await session.commit()
    result = client.portal.call(
        lambda: retire.sweep_retired_storage(
            client.test_request_factory, checkpoint=checkpoint_room
        )
    )
    assert result == {"completed": 0, "pending": 1}
    assert [call.args[3] for call in action.await_args_list] == ["prepare"]
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        assert operation.state == "pending"
        assert "still finishing" in operation.last_error
        (await session.get(AgentTurn, turn.id)).stopped_at = datetime.now(UTC)
        operation.due_at = datetime.now(UTC)
        await session.commit()
    # A fresh worker/session resumes the same recorded resource, not a fresh inventory.
    assert client.portal.call(
        lambda: retire.sweep_retired_storage(
            client.test_request_factory, checkpoint=checkpoint_room
        )
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
        lambda: retire.sweep_retired_storage(
            client.test_request_factory, checkpoint=checkpoint_room
        )
    )
    async with client.test_factory() as session:
        assert (await session.get(RoomCleanup, cleanup_id)).state == "preparing"
        with pytest.raises(ConflictError, match="正在停下"):
            await TopicService(session).unarchive(
                room_id,
                by="owner",
                sessions=AgentSessionService(session),
                events=platform_line,
            )


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
        lambda: retire.sweep_retired_storage(
            client.test_request_factory, checkpoint=checkpoint_room
        )
    )
    async with client.test_factory() as session:
        assert (await session.get(RoomCleanup, cleanup_id)).state == "claimed"
        await TopicService(session).unarchive(
            room_id,
            by="owner",
            sessions=AgentSessionService(session),
            events=platform_line,
        )
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
        lambda: retire.sweep_retired_storage(
            client.test_request_factory, checkpoint=checkpoint_room
        )
    )
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, cleanup_id)
        assert operation.state == "claimed" and operation.last_error == "device offline"
        room = await TopicService(session).unarchive(
            room_id,
            by="owner",
            sessions=AgentSessionService(session),
            events=platform_line,
        )
        assert isinstance(room.resource_id, uuid.UUID) and room.resource_id != room_id
        await session.commit()
    action.side_effect = None
    assert client.portal.call(
        lambda: retire.sweep_retired_storage(
            client.test_request_factory, checkpoint=checkpoint_room
        )
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

    async def slow_pass(sessions, checkpoint):
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
    first = asyncio.create_task(
        retire.sweep_retired_storage(business_db_factory, checkpoint=checkpoint_room)
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    # Two more asks while the first is still running: neither starts a sweep.
    waiting = [
        asyncio.create_task(
            retire.sweep_retired_storage(
                business_db_factory, checkpoint=checkpoint_room
            )
        )
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
        lambda: retire.sweep_retired_storage(
            client.test_request_factory, checkpoint=checkpoint_room
        )
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
        lambda: retire.sweep_retired_storage(
            client.test_request_factory, checkpoint=checkpoint_room
        )
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
    """Give each machine a device record, as enrolling one does; a machine
    already enrolled keeps the one it has."""
    from app.domain.device.models import DeviceRow
    from app.domain.device.supply import Supply

    owner = await registered(session, "owner")
    known = set(
        await session.scalars(
            select(DeviceRow.device_id).where(DeviceRow.device_id.in_(device_ids))
        )
    )
    for device_id in (d for d in device_ids if d not in known):
        session.add(
            DeviceRow(
                device_id=device_id,
                name=device_id,
                token_digest=digest_token(f"{device_id}-token"),
                owner_user_id=owner,
                team_id=await enrollment_team(sql_device_service(session), owner),
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


async def _sweep_with_device(client, monkeypatch, device_id: str, *, enrolled_too):
    """Archive a room whose inventory named ``device_id``, which never answers."""
    from app.domain.agent.device_hub import DeviceOffline

    _room_id, cleanup_id = await archived_room(client, monkeypatch)
    async with client.test_factory() as session:
        resource_id = str((await session.get(RoomCleanup, cleanup_id)).resource_id)
        if enrolled_too:
            await enrolled(session, device_id)
            await session.commit()

    async def offline(device, *args, **kwargs):
        raise DeviceOffline(device)

    monkeypatch.setattr(retire.device_hub, "exec", offline)
    monkeypatch.setattr(
        retire,
        "_inventory",
        AsyncMock(
            return_value=[
                {"kind": "device", "device_id": device_id, "resource_id": resource_id}
            ]
        ),
    )
    return _sweep(client)


async def test_a_machine_removed_after_the_inventory_does_not_hold_the_cleanup(
    client, monkeypatch
):
    """The inventory named a pool host that the pool later released, which
    took its device record with it. Nothing of the room is left there, so the
    cleanup finishes instead of waiting for a machine that cannot come back."""
    assert await _sweep_with_device(
        client, monkeypatch, "released-host", enrolled_too=False
    ) == {"completed": 1, "pending": 0}


async def test_an_offline_machine_named_by_the_inventory_is_still_waited_for(
    client, monkeypatch
):
    assert await _sweep_with_device(
        client, monkeypatch, "away-host", enrolled_too=True
    ) == {"completed": 0, "pending": 1}


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


class PrivateBucket:
    """The private bucket, as far as a room's cleanup uses it: a presigned PUT
    URL a machine sends to over HTTP, and what the bucket then reports."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.refusing = False
        # Takes the PUT but reports another size for it, as a truncated or
        # replaced object would read back.
        self.misreporting = False
        bucket = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                if bucket.refusing:
                    self.send_response(503)
                else:
                    bucket.objects[self.path.lstrip("/")] = body
                    self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    async def presign(self, key: str, operation: str, expires_s: int) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/{key}"

    async def stat(self, key: str):
        body = self.objects.get(key)
        if body is None:
            return None
        return (len(body) + self.misreporting, hashlib.md5(body).hexdigest())

    def transcripts(self) -> dict[str, bytes]:
        return {
            key: body
            for key, body in self.objects.items()
            if key.startswith("transcripts/")
        }


@pytest.fixture
def private_bucket(monkeypatch):
    bucket = PrivateBucket()
    monkeypatch.setattr(device_storage, "private_storage", lambda: bucket)
    # The bucket here is on loopback, past any proxy the environment names.
    monkeypatch.setenv("no_proxy", "*")
    yield bucket
    bucket.server.shutdown()


@pytest.fixture
async def session_host_room(client, monkeypatch, tmp_path, private_bucket):
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
    harness = machine / ".cheese/harness" / str(project_id) / str(resource_id)
    return SimpleNamespace(
        room_id=room_id,
        cleanup_id=cleanup_id,
        home=home,
        archive=archive,
        harness=harness,
        bucket=private_bucket,
    )


def _with_codex_and_pi(room) -> dict[str, bytes]:
    """Give the room a Codex session and a pi one (and a pi subagent's), where
    those harnesses write them; what the archive holds of all its sessions."""
    codex = room.harness / "codex/seat-a/codex/sessions/2026/10/19"
    pi = room.harness / "pi/seat-b/sessions"
    child = room.harness / "pi/seat-b/subagents/agent-c/sessions"
    for directory in (codex, pi, child):
        directory.mkdir(parents=True)
    (codex / "rollout-1.jsonl").write_bytes(b'{"said":"codex"}\n')
    (pi / "s2.jsonl").write_bytes(b'{"said":"pi"}\n')
    (child / "s3.jsonl").write_bytes(b'{"said":"pi child"}\n')
    return {
        "projects/-room/s1.jsonl": b'{"said":"main"}\n',
        "projects/-room/s1/subagents/agent-a.jsonl": b'{"said":"sub"}\n',
        "codex/seat-a/codex/sessions/2026/10/19/rollout-1.jsonl": b'{"said":"codex"}\n',
        "pi/seat-b/sessions/s2.jsonl": b'{"said":"pi"}\n',
        "pi/seat-b/subagents/agent-c/sessions/s3.jsonl": b'{"said":"pi child"}\n',
    }


def _bundled(body: bytes, tmp_path: Path) -> dict[str, bytes]:
    copy = tmp_path / f"{uuid.uuid4()}.tar.gz"
    copy.write_bytes(body)
    return _archived_files(copy)


def _sweep(client) -> dict:
    return client.portal.call(
        lambda: retire.sweep_retired_storage(
            client.test_request_factory, checkpoint=checkpoint_room
        )
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
        operation.resources = [
            {**entry, "expires_at": operation.due_at.isoformat()}
            if entry.get("retained")
            else entry
            for entry in operation.resources
        ]
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
        reopened = await TopicService(session).unarchive(
            room.room_id,
            by="owner",
            sessions=AgentSessionService(session),
            events=platform_line,
        )
        assert reopened.resource_id not in {None, room.room_id}
        await session.commit()
        # Nothing is restored into the new generation; the kept copy stays put.
        assert (await session.get(RoomCleanup, room.cleanup_id)).state == "retained"
    assert room.archive.exists()
    await _make_due(client, room.cleanup_id)
    assert _sweep(client) == {"completed": 1, "pending": 0}
    assert not room.archive.exists()


async def test_an_archived_rooms_transcripts_are_in_the_bucket_after_they_expire(
    client, session_host_room, tmp_path
):
    room = session_host_room
    sessions = _with_codex_and_pi(room)
    assert _sweep(client) == {"completed": 1, "pending": 0}
    # In the bucket from the removal on, not only once the host's copy is due.
    assert room.bucket.transcripts() != {}
    await _make_due(client, room.cleanup_id)
    assert _sweep(client) == {"completed": 1, "pending": 0}

    # The host's copy is gone; the bucket's holds every session of the room,
    # Claude Code's, Codex's and pi's.
    assert not room.archive.exists()
    [body] = room.bucket.transcripts().values()
    assert _bundled(body, tmp_path) == sessions


async def test_transcripts_the_bucket_did_not_take_outlive_their_expiry(
    client, session_host_room
):
    room = session_host_room
    room.bucket.refusing = True
    _sweep(client)
    await _make_due(client, room.cleanup_id)
    _sweep(client)

    # The host's copy is the only one, so it stays, and is due again.
    assert room.archive.exists()
    assert room.bucket.transcripts() == {}
    async with client.test_factory() as session:
        assert (await session.get(RoomCleanup, room.cleanup_id)).state == "retained"


async def test_transcripts_the_bucket_holds_wrongly_outlive_their_expiry(
    client, session_host_room
):
    room = session_host_room
    room.bucket.misreporting = True
    _sweep(client)
    await _make_due(client, room.cleanup_id)
    _sweep(client)

    # The bucket took a PUT but does not hold what the host sent, so the
    # host's copy is still the only good one.
    assert room.archive.exists()
    async with client.test_factory() as session:
        assert (await session.get(RoomCleanup, room.cleanup_id)).state == "retained"


@pytest.mark.parametrize("failure", ["upload", "verification", "missing_bucket"])
async def test_transcript_failure_alerts_once_and_backs_off(
    client, session_host_room, monkeypatch, caplog, failure
):
    room = session_host_room
    room.bucket.refusing = failure == "upload"
    room.bucket.misreporting = failure == "verification"
    if failure == "missing_bucket":
        monkeypatch.setattr(device_storage, "_private_bucket", lambda: None)
    with caplog.at_level("DEBUG"):
        _sweep(client)
        async with client.test_factory() as session:
            operation = await session.get(RoomCleanup, room.cleanup_id)
            assert operation.last_error
            assert operation.failures == 1
            first_due = operation.due_at
            assert first_due > datetime.now(UTC)
            operation.due_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        _sweep(client)
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, room.cleanup_id)
        assert operation.failures == 2
        assert operation.due_at > first_due
    errors = [
        entry
        for entry in caplog.records
        if entry.levelno >= 40
        and entry.name in {"cheesex.topic.retire", "cheesex.agent.device_storage"}
    ]
    assert len(errors) == 1
    assert room.archive.exists()
    room.bucket.refusing = False
    room.bucket.misreporting = False
    monkeypatch.setattr(device_storage, "_private_bucket", lambda: room.bucket)
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, room.cleanup_id)
        operation.due_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    _sweep(client)
    async with client.test_factory() as session:
        operation = await session.get(RoomCleanup, room.cleanup_id)
        assert operation.last_error is None
        assert operation.failures == 0
        assert operation.state == "retained"
        assert operation.due_at > datetime.now(UTC) + timedelta(days=29)
    assert room.archive.exists()


async def test_a_harness_session_linked_out_of_the_harness_is_not_archived(
    client, session_host_room, tmp_path
):
    room = session_host_room
    sessions = _with_codex_and_pi(room)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "codex/sessions").mkdir(parents=True)
    (elsewhere / "codex/sessions/other.jsonl").write_bytes(b'{"not":"ours"}\n')
    (room.harness / "codex/seat-z").symlink_to(elsewhere, target_is_directory=True)
    _sweep(client)

    # Every session the harnesses wrote, and nothing the link points at.
    [body] = room.bucket.transcripts().values()
    assert _bundled(body, tmp_path) == sessions


async def test_transcripts_kept_before_they_were_sent_are_sent_before_they_go(
    client, session_host_room, tmp_path
):
    room = session_host_room
    room.bucket.refusing = True
    _sweep(client)
    kept = _archived_files(room.archive)
    room.bucket.refusing = False
    await _make_due(client, room.cleanup_id)
    assert _sweep(client) == {"completed": 1, "pending": 0}

    assert not room.archive.exists()
    [body] = room.bucket.transcripts().values()
    assert _bundled(body, tmp_path) == kept


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


async def test_a_claimed_room_whose_machine_is_offline_waits_for_it_without_alarm(
    client, session_host_room, monkeypatch, caplog
):
    """A machine that is away is the ordinary reason an archived room waits.
    After the claim as before it: the room stays, the wait is reported when it
    starts and not again every pass, and nothing reads it as a failure. Once
    the machine is back the room is cleaned up."""
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
    online = retire.device_hub.exec

    async def away(device_id, *args, **kwargs):
        raise DeviceOffline(device_id)

    monkeypatch.setattr(retire.device_hub, "exec", away)
    with caplog.at_level("DEBUG", logger="cheesex.topic.retire"):
        assert _sweep(client) == {"completed": 0, "pending": 1}
        assert _sweep(client) == {"completed": 0, "pending": 1}
    assert room.home.exists()
    async with client.test_factory() as session:
        assert (await session.get(RoomCleanup, room.cleanup_id)).state == "claimed"
    records = [r for r in caplog.records if r.name == "cheesex.topic.retire"]
    assert not [r for r in records if r.levelname == "ERROR"]
    assert len([r for r in records if r.levelname == "WARNING"]) == 1

    monkeypatch.setattr(retire.device_hub, "exec", online)
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
        other_project = await project_service(session).create(
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
                SandboxHome(
                    host_id=host.id,
                    spec="standard",
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
                select(SandboxHome.topic_id).where(SandboxHome.host_id == host_id)
            )
        )
        assert homes == [other_room.id]
        assert (await session.get(CloudHost, host_id)).released_at is None


async def test_a_room_that_became_a_task_is_still_cleaned_up(client, monkeypatch):
    """A room archived before its cleanup finished became a closed task: its
    row is gone, its conversation stays. Its cleanup is not called off for
    that, and the old generation's homes go with it."""
    from sqlalchemy import text

    from app.domain.agent_session.services import AgentSessionService

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
        host = CloudHost(
            machine_id=None,
            customer_id=1,
            account_id=1,
            offering_id=1,
            hostname=f"host-{uuid.uuid4().hex[:8]}",
            login_user="cheese",
            cores=1,
            memory_mb=4096,
            disk_gb=20,
            status=MachineStatus.provisioning,
            ai_mode="none",
            ai_status=AiStatus.unknown,
        )
        session.add(host)
        await session.flush()
        # Placed on a host that never came up: no directory anywhere.
        session.add(
            SandboxHome(
                host_id=host.id,
                spec="standard",
                project_id=operation.project_id,
                topic_id=room_id,
                room_resource_id=str(operation.resource_id),
                resource_id=resource,
                session_id=conversation.id,
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

    assert _sweep(client) == {"completed": 1, "pending": 0}
    async with client.test_factory() as session:
        assert (await session.get(RoomCleanup, cleanup_id)).state == "complete"
        assert (
            await session.scalar(
                select(SandboxHome.id).where(SandboxHome.topic_id == room_id)
            )
        ) is None


async def test_a_cleanup_held_for_the_same_reason_backs_off(client, monkeypatch):
    """Held again for the same reason, a cleanup waits longer before the next
    try, an hour at most; a new reason starts again at a minute."""
    room_id, cleanup_id = await archived_room(client, monkeypatch)
    entry = {"kind": "device", "device_id": "fixture", "resource_id": str(room_id)}
    monkeypatch.setattr(retire, "_inventory", AsyncMock(return_value=[entry]))
    reasons = ["a process still holds the room"]

    async def step(device_id, project_id, resource_id, name, *rest):
        if name == "prepare":
            raise RuntimeError(reasons[-1])

    monkeypatch.setattr(retire, "_device_action", AsyncMock(side_effect=step))

    async def wait() -> timedelta:
        async with client.test_factory() as session:
            operation = await session.get(RoomCleanup, cleanup_id)
            waited = operation.due_at - datetime.now(UTC)
            operation.due_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
            return waited

    waits = []
    for _ in range(9):
        assert _sweep(client) == {"completed": 0, "pending": 1}
        waits.append(await wait())
    assert waits[1] > waits[0] * 1.5, "同一个原因第二次没有等得更久"
    assert all(w <= retire.RETRY_MOST for w in waits)
    assert waits[-1] > timedelta(minutes=50), "一直失败也没有拉到一小时"

    reasons.append("a new reason")
    assert _sweep(client) == {"completed": 0, "pending": 1}
    assert await wait() <= retire.RETRY_FIRST, "换了原因还在按老的间隔等"
