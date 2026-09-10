"""Durable archived-room cleanup, independent of backend process lifetime."""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import resource_cleanup
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_provider import device_home_dir, list_device_storage
from app.domain.agent.harness.claude_code import event_drain
from app.domain.agent.models import AgentTurn
from app.domain.device.models import DeviceRow
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.machine.services import MachineService
from app.domain.room_task.services import TaskService
from app.domain.topic.models import RoomCleanup, Topic, TopicStatus
from app.domain.topic.repositories import TopicRepository
from app.domain.workspace import service as ws

logger = logging.getLogger("cheesex.topic.retire")


async def _device_action(
    device: str,
    project: uuid.UUID,
    resource: str,
    action: str,
    cleanup_id: uuid.UUID,
    receipts: list | None = None,
) -> None:
    result = await device_hub.exec(
        device,
        ["python3", "-", action, str(project), resource, str(cleanup_id)],
        stdin=Path(resource_cleanup.__file__).read_text(),
        timeout=60,
        env={"CHEESE_TRANSCRIPT_RECEIPTS": json.dumps(receipts or [])},
    )
    if result.get("exit") != 0 or result.get("truncated"):
        raise RuntimeError(
            str(result.get("stderr") or "device cleanup check failed")[-1500:]
        )


async def _flush_transcripts(
    project: uuid.UUID,
    topic: uuid.UUID,
    entry: dict,
    session: AsyncSession,
    cleanup_id: uuid.UUID,
) -> list:
    # Use the same collector even when the room last ran an older launcher.
    home = device_home_dir(project, uuid.UUID(entry["resource_id"]))
    token = mint_scoped_token(project_id=str(project), topic_id=str(topic), ttl_s=3600)
    base = settings.connector_public_base.rstrip("/")
    device = await session.get(DeviceRow, entry["device_id"])
    if device and device.supply == Supply.cloud and device.cloud_control_private:
        base = "http://127.0.0.1:18080"
    setup = (
        f'if [ ! -d "{home}" ]; then printf "[]"; exit 0; fi; '
        f'export CHEESE_COLLECT_HOME="{home}"; exec python3 -'
    )
    source = Path(event_drain.__file__).read_text()
    source = source[: source.index('if __name__ == "__main__":')]
    source += """\nhome = Path(os.environ["CHEESE_COLLECT_HOME"])
script = home / ".claude/cheese-drain"
script.parent.mkdir(parents=True, exist_ok=True)
values = {"CHEESE_HOOK_SPOOL": str(home / ".claude/cheese-spool"),
          "CHEESE_HOOK_URL": os.environ["CHEESE_CLEANUP_HOOK_URL"],
          "CHEESE_CLEANUP_ID": os.environ["CHEESE_CLEANUP_ID"],
          "CHEESE_TOKEN": os.environ["CHEESE_CLEANUP_TOKEN"]}
print(json.dumps(collect_transcripts(script, values, flush=True)))
"""
    result = await device_hub.exec(
        entry["device_id"],
        ["sh", "-c", setup],
        stdin=source,
        env={
            "CHEESE_CLEANUP_TOKEN": token,
            "CHEESE_CLEANUP_HOOK_URL": f"{base}/sandbox/hooks/{topic}",
            "CHEESE_CLEANUP_ID": str(cleanup_id),
        },
        timeout=900,
    )
    if result.get("exit") != 0 or result.get("truncated"):
        raise RuntimeError("final transcript collection or confirmation failed")
    return json.loads(result.get("stdout") or "[]")


async def _inventory(session, operation: RoomCleanup, inventory: dict) -> list[dict]:
    resource_ids = {str(operation.resource_id)}
    resource_ids.update(
        str(task.id)
        for task, *_ in await TaskService(session).threads_for_room(
            operation.topic_id, limit=0
        )
    )
    entries = {}
    room = await session.get(Topic, operation.topic_id)
    placement = room.session_placement if room else None
    if placement and placement["resource_id"] == str(operation.resource_id):
        center = placement["device_id"]
        if not device_hub.is_online(center) or center not in inventory:
            raise RuntimeError(
                "room's session device is offline or its inventory failed"
            )
        entries[(center, str(operation.resource_id))] = {
            "kind": "device",
            "device_id": center,
            "resource_id": str(operation.resource_id),
        }
    binding = await sql_device_service(session).topic_binding(operation.topic_id)
    if binding is not None:
        if (
            not device_hub.is_online(binding.device_id)
            or binding.device_id not in inventory
        ):
            raise RuntimeError("room's bound device is offline or its inventory failed")
        entries[(binding.device_id, str(operation.resource_id))] = {
            "kind": "device",
            "device_id": binding.device_id,
            "resource_id": str(operation.resource_id),
        }
    for device_id, paths in inventory.items():
        for _kind, project, resource in paths:
            if project == str(operation.project_id) and resource in resource_ids:
                entries[(device_id, resource)] = {
                    "kind": "device",
                    "device_id": device_id,
                    "resource_id": resource,
                }
    result = list(entries.values())
    machine = await MachineService(session).topic_machine(operation.topic_id)
    if machine is not None:
        if machine.device_id is not None:
            if (
                not device_hub.is_online(machine.device_id)
                or machine.device_id not in inventory
            ):
                raise RuntimeError("Cloud device is offline or its inventory failed")
            if not any(entry.get("device_id") == machine.device_id for entry in result):
                result.append(
                    {
                        "kind": "device",
                        "device_id": machine.device_id,
                        "resource_id": str(operation.resource_id),
                    }
                )
            shared = await sql_device_service(session).list_topic_bindings(
                machine.device_id
            )
            if any(pin.topic_id != operation.topic_id for pin in shared):
                raise RuntimeError("Cloud machine has another room's binding")
            covered = {
                (str(operation.project_id), entry["resource_id"])
                for entry in result
                if entry.get("device_id") == machine.device_id
            }
            if any(
                (project, resource) not in covered
                for _kind, project, resource in inventory[machine.device_id]
            ):
                raise RuntimeError(
                    "Cloud machine contains unrecognized room directories"
                )
        result.append({"kind": "machine", "id": str(machine.id)})
    trees = await TaskService(session).list_in_project(operation.project_id)
    own = {
        operation.topic_id,
        *(tree.id for tree in trees if tree.room_id == operation.topic_id),
    }
    other = {tree.id for tree in trees if tree.room_id != operation.topic_id}
    other.update(
        await session.scalars(
            select(Topic.id).where(
                Topic.project_id == operation.project_id,
                Topic.id != operation.topic_id,
            )
        )
    )
    for project, prefix, path in await asyncio.to_thread(ws.topic_worktrees_on_disk):
        if project != operation.project_id or not any(
            item.hex[:8] == prefix for item in own
        ):
            continue
        if any(item.hex[:8] == prefix for item in other):
            raise RuntimeError("backend worktree prefix has ambiguous ownership")
        target = path.parent / ".retired" / str(operation.id) / path.name
        result.append({"kind": "worktree", "path": str(path), "retired": str(target)})
    return result


def _park_worktree(entry: dict) -> None:
    source, target = Path(entry["path"]), Path(entry["retired"])
    if not target.exists():
        if not source.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        result = resource_cleanup.run_command(
            ["git", "worktree", "move", str(source), str(target)], cwd=source
        )
        if result.returncode:
            raise RuntimeError("could not isolate the backend checkout for cleanup")
    # Free the room branch before claim: reopening can check it out while
    # deletion of another old resource is still waiting for its device.
    result = resource_cleanup.run_command(["git", "checkout", "--detach"], cwd=target)
    if result.returncode:
        raise RuntimeError("could not detach the retired backend checkout")


async def sweep_retired_storage(sessions: SessionFactory) -> dict[str, int]:
    counts = {"completed": 0, "pending": 0}
    async with sessions() as session:
        engine = session.bind
        ids = list(
            await session.scalars(
                select(RoomCleanup.id).where(
                    RoomCleanup.state.in_(["pending", "preparing", "claimed"]),
                    RoomCleanup.due_at <= datetime.now(UTC),
                )
            )
        )
    if not ids:
        return counts
    inventory = {}
    for device_id in device_hub.online_device_ids():
        try:
            inventory[device_id] = await list_device_storage(device_id)
        except Exception:
            logger.exception("cleanup device inventory failed device=%s", device_id)
    assert isinstance(engine, AsyncEngine)
    for cleanup_id in ids:
        # Keep one physical connection across commits: a session advisory lock
        # must not be returned to the pool while a device command is in flight.
        async with engine.connect() as connection:
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                key = {"key": f"room-cleanup:{cleanup_id}"}
                locked = (
                    await session.execute(
                        text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                        key,
                    )
                ).scalar()
                if not locked:
                    continue
                try:
                    await _advance(session, cleanup_id, inventory)
                    operation = await session.get(RoomCleanup, cleanup_id)
                    counts[
                        "completed"
                        if operation and operation.state == "complete"
                        else "pending"
                    ] += 1
                except Exception as exc:
                    await session.rollback()
                    operation = await session.get(RoomCleanup, cleanup_id)
                    if operation is not None:
                        operation.last_error = str(exc)[:2048]
                        await session.commit()
                    logger.exception("cleanup failed operation=%s", cleanup_id)
                    counts["pending"] += 1
                finally:
                    # Closing the physical connection also releases the lock on
                    # cancellation, without leaking a locked connection into a pool.
                    await connection.invalidate()
    return counts


async def _advance(session, cleanup_id: uuid.UUID, inventory: dict) -> None:
    operation = await session.get(RoomCleanup, cleanup_id)
    if operation is None or operation.state in {"cancelled", "complete"}:
        return
    retry_claim = operation.state == "claimed"
    room = await TopicRepository(session).lock(operation.topic_id)
    if operation.state == "pending":
        if (
            room is None
            or room.status != TopicStatus.archived
            or room.cleanup_id != operation.id
        ):
            operation.state = "cancelled"
            await session.commit()
            return
        try:
            if not operation.resources:
                operation.resources = await _inventory(session, operation, inventory)
        except Exception as exc:
            operation.last_error = str(exc)[:2048]
            await session.commit()
            return
        operation.state = "preparing"
    await session.commit()
    logger.info(
        "cleanup start operation=%s room=%s state=%s",
        cleanup_id,
        operation.topic_id,
        operation.state,
    )
    if operation.state == "preparing":
        stopped = False
        parking_started = any(
            entry["kind"] == "worktree" and Path(entry["retired"]).exists()
            for entry in operation.resources
        )
        try:
            for entry in operation.resources:
                if entry["kind"] == "device":
                    await _device_action(
                        entry["device_id"],
                        operation.project_id,
                        entry["resource_id"],
                        "prepare",
                        operation.id,
                    )
            stopped = True
            resources = [dict(entry) for entry in operation.resources]
            for entry in resources:
                if entry["kind"] == "device":
                    await _device_action(
                        entry["device_id"],
                        operation.project_id,
                        entry["resource_id"],
                        "publication",
                        operation.id,
                    )
                    entry["transcripts"] = await _flush_transcripts(
                        operation.project_id,
                        operation.topic_id,
                        entry,
                        session,
                        operation.id,
                    )
            active = await session.scalar(
                select(AgentTurn.id)
                .where(
                    AgentTurn.topic_id == operation.topic_id,
                    AgentTurn.stopped_at.is_(None),
                )
                .limit(1)
            )
            if active is not None:
                raise RuntimeError(
                    "room work is still finishing or persisting its result"
                )
            for entry in resources:
                if entry["kind"] == "worktree":
                    target = Path(entry["retired"])
                    path = target if target.exists() else Path(entry["path"])
                    await asyncio.to_thread(resource_cleanup.check_no_writers, [path])
                    await asyncio.to_thread(
                        resource_cleanup.check_published, path, canonical=True
                    )
            for entry in resources:
                if entry["kind"] == "worktree":
                    parking_started = True
                    await asyncio.to_thread(_park_worktree, entry)
            operation.resources = resources
            for screen in list(device_hub.screens_for_topic(operation.topic_id)):
                await device_hub.close_screen(screen.device_id, screen.sid)
            operation.state = "claimed"
            operation.last_error = None
            if room is not None:
                room.transcripts_archived_at = datetime.now(UTC)
            await session.commit()
        except Exception as exc:
            operation.state = (
                "pending" if stopped and not parking_started else "preparing"
            )
            operation.last_error = str(exc)[:2048]
            await session.commit()
            logger.warning("cleanup pending operation=%s reason=%s", cleanup_id, exc)
            return
    for entry in operation.resources:
        if entry.get("removed"):
            continue
        if entry["kind"] == "device":
            if retry_claim:
                # A delayed append may have prevented the previous removal.
                # Reconcile only the recorded old generation, even after reopen.
                await _device_action(
                    entry["device_id"],
                    operation.project_id,
                    entry["resource_id"],
                    "prepare",
                    operation.id,
                )
                entry["transcripts"] = await _flush_transcripts(
                    operation.project_id,
                    operation.topic_id,
                    entry,
                    session,
                    operation.id,
                )
                operation.resources = [dict(item) for item in operation.resources]
                await session.commit()
            await _device_action(
                entry["device_id"],
                operation.project_id,
                entry["resource_id"],
                "remove",
                operation.id,
                entry.get("transcripts"),
            )
        elif entry["kind"] == "worktree":
            target = Path(entry["retired"])
            # Never return to the old path: reopening may already own it.
            if target.exists():
                await asyncio.to_thread(resource_cleanup.check_no_writers, [target])
                await asyncio.to_thread(
                    resource_cleanup.check_published, target, canonical=True
                )
                if not await asyncio.to_thread(
                    ws.remove_worktree, operation.project_id, target
                ):
                    raise RuntimeError("backend checkout removal failed")
        elif entry["kind"] == "machine":
            await MachineService(session).release_archived_machine(
                uuid.UUID(entry["id"])
            )
        operation.resources = [
            {**item, "removed": True} if item == entry else item
            for item in operation.resources
        ]
        await session.commit()
    operation.state = "complete"
    operation.last_error = None
    await session.commit()
    logger.info("cleanup complete operation=%s room=%s", cleanup_id, operation.topic_id)
