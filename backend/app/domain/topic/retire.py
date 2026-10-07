"""Durable archived-room cleanup, independent of backend process lifetime."""

import asyncio
import json
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.background import spawn
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.agent import resource_cleanup
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_storage import (
    KEEP_ROOM_TIMEOUT_S,
    expire_room_files,
    keep_room_files_due,
    list_device_storage,
    record_room_files,
    room_files_upload_url,
)
from app.domain.agent.models import AgentTurn
from app.domain.agent_session.services import AgentSessionService
from app.domain.conversation.services import of_room
from app.domain.device.wiring import sql_device_service
from app.domain.machine.services import HostPool
from app.domain.repository import service as ws
from app.domain.room_task.services import TaskService
from app.domain.topic.models import RoomCleanup, Topic, TopicStatus
from app.domain.topic.repositories import TopicRepository

logger = logging.getLogger("cheesex.topic.retire")

# How long an archived room's Claude Code transcripts stay on the session host
# after its cleanup removes the room's home. A window for debugging what an
# agent did after the fact, and no longer: transcripts are sensitive user data,
# and nothing needs them to carry the work on — the room's chat, its living doc
# and the action timeline stay with the room. A reopened room starts a new
# session and does not read them either.
TRANSCRIPT_RETENTION = timedelta(days=30)

# A cleanup that fails again for the same reason is tried again later and
# later: a minute, then twice that each time, an hour at most. What holds one
# (a device that is gone, a process that will not leave) seldom goes away
# within the minute, and a sweep that tried every held cleanup every minute ran
# two commands on their devices for each of them, all day (dev, 2026-10-07: 63
# of them). A new reason starts again at a minute, and one that goes away is
# found within the hour.
RETRY_FIRST = timedelta(minutes=1)
RETRY_MOST = timedelta(hours=1)


def _held_back(operation: RoomCleanup, reason: str) -> bool:
    """Record that ``operation`` failed for ``reason`` and when to try again.
    Whether the reason is new, which is when it is worth saying."""
    changed = reason != operation.last_error
    operation.failures = 1 if changed else (operation.failures or 0) + 1
    operation.last_error = reason
    operation.due_at = datetime.now(UTC) + min(
        RETRY_MOST, RETRY_FIRST * 2 ** min(operation.failures - 1, 10)
    )
    return changed


def _went_on(operation: RoomCleanup) -> None:
    """The cleanup got past what held it."""
    operation.last_error = None
    operation.failures = 0


def _keeps_transcripts(entry: dict) -> bool:
    """Whether this resource's transcripts are kept when its home is removed.

    Every Claude Code session runs on the central session host, so a room's
    transcripts are in its home there and in no other device's."""
    return (
        entry["kind"] == "device"
        and settings.agent_session_device_id is not None
        and entry["device_id"] == settings.agent_session_device_id
    )


async def _destroyed(session, entry: dict) -> bool:
    """A session's sandbox on one of the pool's cloud hosts that the pool has
    destroyed since the inventory named it (``machine/lifecycle.py``), or gave
    up with its host. Nothing of it is left there to stop, check or remove,
    and its host may be gone: asking a host that will never answer again
    would keep the cleanup failing "device … is offline" on every sweep."""
    return entry["kind"] == "device" and await HostPool(session).destroyed(
        entry["device_id"], entry["resource_id"]
    )


async def _device_action(
    device: str,
    project: uuid.UUID,
    resource: str,
    action: str,
    cleanup_id: uuid.UUID,
    room: uuid.UUID | None = None,
    keep_url: str | None = None,
) -> dict | None:
    """Run one cleanup action on the device; its last line of output, which
    every action prints as JSON. ``keep_url``: where ``remove`` sends a room's
    files that are on no forge (``device_storage.record_room_files``)."""
    result = await device_hub.exec(
        device,
        [
            "python3",
            "-",
            action,
            str(project),
            resource,
            str(cleanup_id),
            str(room) if room else "-",
        ],
        stdin=Path(resource_cleanup.__file__).read_text(),
        timeout=KEEP_ROOM_TIMEOUT_S if keep_url is not None else 60,
        env={"CHEESE_KEEP_URL": keep_url} if keep_url is not None else None,
    )
    if result.get("exit") == resource_cleanup.STILL_RUNNING_EXIT and not result.get(
        "truncated"
    ):
        raise resource_cleanup.StillRunning(str(result.get("stderr") or "").strip())
    if result.get("exit") != 0 or result.get("truncated"):
        raise RuntimeError(
            str(result.get("stderr") or "device cleanup check failed")[-1500:]
        )
    lines = str(result.get("stdout") or "").strip().splitlines()
    return json.loads(lines[-1]) if lines else None


async def _inventory(session, operation: RoomCleanup, inventory: dict) -> list[dict]:
    from app.domain.agent_session.models import AgentSession

    resource_ids = {str(operation.resource_id)}
    resource_ids.update(
        str(task.id)
        for task in await TaskService(session).list_in_room(operation.topic_id)
    )
    entries = {}
    sessions = list(
        await session.scalars(
            select(AgentSession).where(
                of_room(AgentSession.conversation_id, operation.topic_id)
            )
        )
    )
    devices = sql_device_service(session)
    for conversation in sessions:
        leases = [
            *(conversation.execution_request or {}).get("retained_leases", []),
            *([conversation.work_lease] if conversation.work_lease else []),
        ]
        for lease in leases:
            resource_id = lease.get("resource_id")
            device_id = lease.get("device_id")
            if resource_id:
                resource_ids.add(resource_id)
            # A machine whose record is gone was removed: nothing can connect
            # as it again, so nothing of the room is left on it to stop or
            # remove, and waiting for it to come online failed the cleanup on
            # every sweep, forever. An offline machine that still has its
            # record may come back, and is waited for.
            if (
                not device_id
                or await devices.get_device(device_id) is None
                or await HostPool(session).destroyed(device_id, resource_id)
            ):
                continue
            if not device_hub.is_online(device_id) or device_id not in inventory:
                raise RuntimeError(
                    "session work device is offline or its inventory failed"
                )
            if not resource_id:
                # A lease written before leases named their home. The room's
                # homes on this machine are still found below, by the room's
                # resource ids in its inventory.
                continue
            entries[(device_id, resource_id)] = {
                "kind": "device",
                "device_id": device_id,
                "resource_id": resource_id,
            }
    room = await session.get(Topic, operation.topic_id)
    places = (
        await AgentSessionService(session).places_in_room(operation.topic_id)
        if room
        else []
    )
    for place in places:
        if place.resource_id != str(operation.resource_id):
            continue
        center = place.machine
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
    trees = await TaskService(session).list_in_project(operation.project_id)
    own = {
        operation.topic_id,
        *(tree.id for tree in trees if tree.room_id == operation.topic_id),
    }
    # A room that became a task is that task, under the same id: not another
    # owner of its own checkouts.
    other = {
        tree.id
        for tree in trees
        if tree.room_id != operation.topic_id and tree.id != operation.topic_id
    }
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


def _park_worktree(entry: dict, project_id: uuid.UUID) -> None:
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
    # Older Git keeps our sandbox-relative pointer anchored at the old depth.
    ws._git(ws._repo(project_id), "worktree", "repair", str(target))
    # Free the room branch before claim: reopening can check it out while
    # deletion of another old resource is still waiting for its device.
    result = resource_cleanup.run_command(["git", "checkout", "--detach"], cwd=target)
    if result.returncode:
        raise RuntimeError("could not detach the retired backend checkout")


# A cleanup is worked on by one sweep at a time. The lease on the row is what
# says so across processes (the outgoing and incoming backend of a rollout); a
# sweep that dies leaves a lease that expires. It replaced a session advisory
# lock, which pinned one pool connection per cleanup for as long as the device
# work took — with every device reconnect starting a sweep of its own, a restart
# put N sweeps × 74 cleanups on a 35-connection pool (dev, 2026-09-18).
CLEANUP_LEASE = timedelta(minutes=30)
_LEASE_HOLDER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

# One sweep per process. A request to sweep while one is running is not lost:
# the running sweep goes round again when it finishes.
_sweeping = False
_sweep_again = False
_swept: asyncio.Event | None = None
_last_counts = {"completed": 0, "pending": 0}


#: What gives each session of an archived room its one best-effort checkpoint
#: before the cleanup stops them (``machine.session_work.checkpoint_room``).
#: Handed in by whoever starts a sweep: the machine domain is above this one.
Checkpoint = Callable[[AsyncSession, uuid.UUID], Awaitable[None]]


async def sweep_retired_storage(
    sessions: SessionFactory, *, checkpoint: Checkpoint
) -> dict[str, int]:
    """Sweep what is due, and answer for the sweep that covered this call.

    A caller who arrives while one is running does not start a second: the
    running sweep goes round again for it, and this waits for that round
    rather than answering with zeros it did not measure.
    """
    global _sweeping, _sweep_again, _swept, _last_counts
    if _swept is None:
        _swept = asyncio.Event()
    if _sweeping:
        _sweep_again = True
        finished = _swept
        await finished.wait()
        return dict(_last_counts)
    _sweeping = True
    _swept = asyncio.Event()
    finished = _swept
    counts = {"completed": 0, "pending": 0}
    try:
        while True:
            _sweep_again = False
            for key, value in (await _sweep_once(sessions, checkpoint)).items():
                counts[key] += value
            if not _sweep_again:
                return counts
    finally:
        _sweeping = False
        _last_counts = dict(counts)
        finished.set()


async def _sweep_once(
    sessions: SessionFactory, checkpoint: Checkpoint
) -> dict[str, int]:
    counts = {"completed": 0, "pending": 0}
    try:
        await expire_room_files(sessions)
    except Exception:  # noqa: BLE001 — the next sweep tries again
        logger.warning("kept room files not expired", exc_info=True)
    # Sending them can take minutes; the cleanups below do not wait for it.
    spawn(keep_room_files_due(sessions), name="kept room files")
    async with sessions() as session:
        ids = list(
            await session.scalars(
                select(RoomCleanup.id).where(
                    RoomCleanup.state.in_(
                        ["pending", "preparing", "claimed", "retained"]
                    ),
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
            # WARNING for the same reason as the inventory failure in `_advance`
            # below: the usual cause is a device that is offline, which is what
            # the sweep exists to retry. At ERROR every cycle of every waiting
            # room became a Feishu alert, and the budget those spent is taken
            # from the alerts somebody needs to see.
            logger.warning(
                "cleanup device inventory failed device=%s", device_id, exc_info=True
            )
    for cleanup_id in ids:
        if not await _lease(sessions, cleanup_id):
            continue
        try:
            async with sessions() as session:
                try:
                    await _advance(session, cleanup_id, inventory, checkpoint)
                    operation = await session.get(RoomCleanup, cleanup_id)
                    counts[
                        "completed"
                        if operation and operation.state in {"complete", "retained"}
                        else "pending"
                    ] += 1
                except Exception as exc:
                    await session.rollback()
                    operation = await session.get(RoomCleanup, cleanup_id)
                    reason = str(exc)[:2048]
                    changed = operation is None or _held_back(operation, reason)
                    if operation is not None:
                        await session.commit()
                    if isinstance(exc, resource_cleanup.StillRunning):
                        # Something still has the room open: the sweep waits it
                        # out, as it does before the claim (`_advance`), and says
                        # so when what it waits on changes. At ERROR this was an
                        # alert a minute for every room whose last process took
                        # a few minutes to leave, each of which then finished.
                        logger.log(
                            logging.WARNING if changed else logging.DEBUG,
                            "cleanup waiting operation=%s reason=%s",
                            cleanup_id,
                            reason,
                        )
                    else:
                        logger.exception("cleanup failed operation=%s", cleanup_id)
                    counts["pending"] += 1
        finally:
            await _release(sessions, cleanup_id)
    return counts


async def _lease(sessions: SessionFactory, cleanup_id: uuid.UUID) -> bool:
    """Take the cleanup for CLEANUP_LEASE, unless another sweep holds it."""
    now = datetime.now(UTC)
    async with sessions() as session:
        result = await session.execute(
            update(RoomCleanup)
            .where(
                RoomCleanup.id == cleanup_id,
                or_(RoomCleanup.lease_until.is_(None), RoomCleanup.lease_until < now),
            )
            .values(lease_until=now + CLEANUP_LEASE, lease_holder=_LEASE_HOLDER)
        )
        await session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]


async def _release(sessions: SessionFactory, cleanup_id: uuid.UUID) -> None:
    async with sessions() as session:
        await session.execute(
            update(RoomCleanup)
            .where(
                RoomCleanup.id == cleanup_id,
                RoomCleanup.lease_holder == _LEASE_HOLDER,
            )
            .values(lease_until=None, lease_holder=None)
        )
        await session.commit()


async def _advance(
    session, cleanup_id: uuid.UUID, inventory: dict, checkpoint: Checkpoint
) -> None:
    operation = await session.get(RoomCleanup, cleanup_id)
    if operation is None or operation.state in {"cancelled", "complete"}:
        return
    if operation.state == "retained":
        # Reopening the room does not cancel this: the new generation starts a
        # new session and never reads these, so they expire on schedule.
        await _expire_transcripts(session, operation)
        return
    retry_claim = operation.state == "claimed"
    # The first time through, the room's sessions get their one checkpoint.
    first = operation.state == "pending" and not operation.resources
    room = await TopicRepository(session).lock(operation.topic_id)
    if operation.state == "pending":
        # Only a room taken out of the archive wants its machine back. A room
        # whose row is gone (one that became a closed task, which never
        # reopens) leaves its old generation to be removed all the same: the
        # cleanup knows its project, generation and resources by itself.
        if room is not None and (
            room.status != TopicStatus.archived or room.cleanup_id != operation.id
        ):
            operation.state = "cancelled"
            await session.commit()
            return
        try:
            if not operation.resources:
                operation.resources = await _inventory(session, operation, inventory)
        except Exception as exc:
            # Nothing else reports this one. The operation stays `pending`, so the
            # sweeper retries it forever, and setting `last_error` is what drops
            # every later "cleanup start" line to DEBUG — a room that can never be
            # inventoried would otherwise be retried in complete silence.
            #
            # WARNING, not ERROR: `_inventory` raises for an offline device, which
            # is the ordinary reason an archived room waits, and `obs.AlertOnError`
            # would make every sweep cycle of every such room a Feishu alert.
            logger.warning(
                "cleanup inventory failed operation=%s room=%s: %s",
                cleanup_id,
                operation.topic_id,
                exc,
                exc_info=True,
            )
            _held_back(operation, str(exc)[:2048])
            await session.commit()
            return
        operation.state = "preparing"
    await session.commit()
    logger.log(
        # A retry is not news either — `last_error` is set only by an attempt
        # that already failed, so it is exactly "we have been here before".
        logging.DEBUG if operation.last_error else logging.INFO,
        "cleanup start operation=%s room=%s state=%s",
        cleanup_id,
        operation.topic_id,
        operation.state,
    )
    if first:
        await checkpoint(session, operation.topic_id)
    if operation.state == "preparing":
        stopped = False
        parking_started = any(
            entry["kind"] == "worktree" and Path(entry["retired"]).exists()
            for entry in operation.resources
        )
        try:
            for entry in operation.resources:
                if entry["kind"] == "device" and not await _destroyed(session, entry):
                    await _device_action(
                        entry["device_id"],
                        operation.project_id,
                        entry["resource_id"],
                        "prepare",
                        operation.id,
                    )
            stopped = True
            resources = [dict(entry) for entry in operation.resources]
            active = await session.scalar(
                select(AgentTurn.id)
                .where(
                    of_room(AgentTurn.conversation_id, operation.topic_id),
                    AgentTurn.stopped_at.is_(None),
                )
                .limit(1)
            )
            if active is not None:
                raise RuntimeError(
                    "room work is still finishing or persisting its result"
                )
            # The checks and moves below run outside the database; end the
            # transaction so they do not hold a pool connection.
            await session.commit()
            for entry in resources:
                if entry["kind"] == "worktree":
                    target = Path(entry["retired"])
                    if target.exists():
                        # A previous move can finish before repairing its pointer.
                        await asyncio.to_thread(
                            ws._git,
                            ws._repo(operation.project_id),
                            "worktree",
                            "repair",
                            str(target),
                        )
                    path = target if target.exists() else Path(entry["path"])
                    await asyncio.to_thread(resource_cleanup.check_no_writers, [path])
            for entry in resources:
                if entry["kind"] == "worktree":
                    parking_started = True
                    await asyncio.to_thread(_park_worktree, entry, operation.project_id)
            operation.resources = resources
            for screen in list(device_hub.screens_for_topic(operation.topic_id)):
                await device_hub.close_screen(screen.device_id, screen.sid)
            operation.state = "claimed"
            _went_on(operation)
            await session.commit()
        except Exception as exc:
            operation.state = (
                "pending" if stopped and not parking_started else "preparing"
            )
            reason = str(exc)[:2048]
            # Reported when it CHANGES, not on every attempt. A cleanup whose
            # resource is still held retries for as long as something holds it,
            # and an unchanged reason carries nothing a person can act on that
            # the row does not already hold — `last_error` is the current one,
            # queryable, and does not scroll. Four such operations on dev
            # 2026-09-15 were repeating a six-line remote traceback about once a
            # minute each, filling over half of the only window anyone can read
            # the backend's log through, for hours.
            changed = _held_back(operation, reason)
            await session.commit()
            logger.log(
                logging.WARNING if changed else logging.DEBUG,
                "cleanup pending operation=%s reason=%s",
                cleanup_id,
                exc,
            )
            return
    for entry in operation.resources:
        if entry.get("removed"):
            continue
        if entry["kind"] == "device" and not await _destroyed(session, entry):
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
            # A room from before rooms had an executor sends its files to the
            # bucket before they go, and they are kept for a while; nothing
            # else of the home is waited on.
            keep_url = await room_files_upload_url(
                session, entry["device_id"], operation.project_id, entry["resource_id"]
            )
            await session.commit()
            answer = await _device_action(
                entry["device_id"],
                operation.project_id,
                entry["resource_id"],
                "remove",
                operation.id,
                operation.topic_id if _keeps_transcripts(entry) else None,
                keep_url,
            )
            kept = answer.get("kept") if isinstance(answer, dict) else None
            if kept is not None:
                await record_room_files(
                    session,
                    entry["device_id"],
                    operation.project_id,
                    entry["resource_id"],
                    kept,
                )
            # A session home on a cloud host is gone with its directory, and no
            # longer keeps the host.
            await HostPool(session).forget_device_homes(
                entry["device_id"], entry["resource_id"]
            )
        elif entry["kind"] == "worktree":
            target = Path(entry["retired"])
            # Never return to the old path: reopening may already own it.
            if target.exists():
                await asyncio.to_thread(resource_cleanup.check_no_writers, [target])
                if not await asyncio.to_thread(
                    ws.remove_worktree, operation.project_id, target
                ):
                    raise RuntimeError("backend checkout removal failed")
        # Whether transcripts were kept is recorded with the removal, so their
        # expiry follows what was kept even if the session host changes.
        done = {"removed": True}
        if _keeps_transcripts(entry):
            done["retained"] = True
        operation.resources = [
            {**item, **done} if item == entry else item for item in operation.resources
        ]
        await session.commit()
    # Homes of this generation that never held a directory (a session placed
    # on a host that was still coming up) go with the rest.
    await HostPool(session).forget_room_homes(
        operation.topic_id, str(operation.resource_id)
    )
    if any(entry.get("retained") for entry in operation.resources):
        operation.state = "retained"
        operation.due_at = datetime.now(UTC) + TRANSCRIPT_RETENTION
    else:
        operation.state = "complete"
    _went_on(operation)
    await session.commit()
    logger.info(
        "cleanup %s operation=%s room=%s",
        operation.state,
        cleanup_id,
        operation.topic_id,
    )


async def _expire_transcripts(session, operation: RoomCleanup) -> None:
    for entry in operation.resources:
        if not entry.get("retained") or entry.get("expired"):
            continue
        await _device_action(
            entry["device_id"],
            operation.project_id,
            entry["resource_id"],
            "expire",
            operation.id,
            operation.topic_id,
        )
        operation.resources = [
            {**item, "expired": True} if item == entry else item
            for item in operation.resources
        ]
        await session.commit()
    operation.state = "complete"
    _went_on(operation)
    await session.commit()
    logger.info(
        "cleanup transcripts expired operation=%s room=%s",
        operation.id,
        operation.topic_id,
    )
