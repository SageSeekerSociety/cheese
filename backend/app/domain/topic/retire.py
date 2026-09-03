"""What a finished place leaves on disk, and the two things that take it away.

Archiving a room releases its Cloud machine and settles its cards, and until
2026-09 left two things behind for good:

- the tree's git worktree on this box,
  `<workspace_root>/.worktrees/<project>/topic_<hex8>`;
- the place's isolated claude home on the device that ran it,
  `$HOME/.cheese/home/<project>/<place>` — session files and caches, 1-4 GB each.

Nothing removed either. Measured on the dev box on 2026-09-03: 373 worktrees
(141 GB), 118 of them for archived topics and 219 for topics no longer in the
database at all; 162 homes (162 GB), 46 archived and 85 unknown.

Two mechanisms, because archive cannot always reach:

`retire_room_storage` / `retire_thread_storage` run at archive time, from
`TopicService`, after the Cloud machine is released. Best-effort by design —
nothing here may fail an archive, which is a fact about the place and not about
its disk — and every outcome is one INFO line: removed, or left and why.

`sweep_retired_storage` runs on a clock (scheduler/jobs.py) over what archive
could not reach: the device was offline, the process died mid-archive, the
topic was deleted outright, or the place predates archive removing anything.
It walks this box's worktrees and every online device's homes, resolves each
entry to a place, and removes what belongs to nothing or to something archived
longer ago than `topic_home_retention_days`. The retention is the un-archive
window: inside it the work comes back with its session intact, past it from an
empty checkout of the branch.

A home is stored before it is deleted, on both paths. It holds the only copy of
the agent's raw session files (`.claude/projects/**/*.jsonl` — the room's
conversation is in `blocks`, that is not), so `_remove_home` first has the
device ship `.claude/projects` and `.claude/todos` to the platform
(`PUT /connector/transcripts/<project>/<place>`, stored under
`settings.transcripts_dir`, see topic/transcripts.py), records the upload on
the place as `transcripts_archived_at`, and only on a 2xx runs the `rm`. Any
other answer leaves the home where it is with one WARN line, and the sweep
tries again next tick. A home that never ran a session has nothing to keep and
goes without an upload.

The branch is never touched. Its commits are the record; the directory was only
a checkout of them plus uncommitted work, which an archive abandons by definition.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.device.wiring import sql_device_service
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.services import TaskService, WorkTreeService
from app.domain.topic.models import Topic
from app.domain.topic.repositories import TopicRepository
from app.domain.workspace import service as ws

logger = logging.getLogger("cheesex.topic.retire")


# ---- at archive time --------------------------------------------------------


async def retire_room_storage(session: AsyncSession, room: Topic) -> None:
    """Everything on disk that was only ever this room's: every tree it wrote
    to, then its home on the device that ran it."""
    tree_ids = {tree.id for tree in await WorkTreeService(session).history(room.id)}
    # A room's first tree carries the room's own id, and a room from before
    # trees had rows (or that never took work) has only that directory — so the
    # room's own name is always one of them, whatever the table says.
    tree_ids |= {room.id, ws.tree_for_place(room.id)}
    for tree_id in sorted(tree_ids, key=str):
        await _retire_worktree(
            room.project_id,
            ws.tree_worktree_path(room.project_id, tree_id),
            reason=f"room {room.id} archived",
        )
    await _retire_home(session, room.project_id, room.id)


async def retire_thread_storage(session: AsyncSession, thread: Task) -> None:
    """A thread writes to its room's tree, so only its own home is its to lose."""
    await _retire_home(session, thread.project_id, thread.id)


async def _retire_worktree(project_id: uuid.UUID, wt: Path, *, reason: str) -> bool:
    if not wt.exists():
        return True
    # git and rmtree block, and a checkout can be gigabytes: off the event loop,
    # which is serving the archive request this runs under.
    try:
        gone = await asyncio.to_thread(ws.remove_worktree, project_id, wt)
    except Exception:  # noqa: BLE001 — a disk failure must not fail the archive
        logger.warning("retire: worktree=%s outcome=error", wt, exc_info=True)
        return False
    logger.info(
        "retire: worktree=%s outcome=%s reason=%s",
        wt,
        "removed" if gone else "left (removal failed, the sweep will retry)",
        reason,
    )
    return gone


async def _retire_home(
    session: AsyncSession, project_id: uuid.UUID, place_id: uuid.UUID
) -> None:
    from app.domain.agent.device_provider import release_topic_screen

    # Close the screen first, so the device's claude is not holding the home
    # while it is deleted — and the clone under its work dir goes with it.
    # Best-effort and idempotent on its side; a place that never ran on a device
    # has no screen to close.
    try:
        await release_topic_screen(project_id, place_id)
    except Exception:  # noqa: BLE001
        logger.warning("retire: place=%s screen not released", place_id, exc_info=True)
    # The pin is where a place's turns ran, and it is read AFTER the Cloud
    # release on purpose: a Cloud topic's pin is dropped with its machine, and
    # its home dies with the VM — only a self-hosted device is left to clean.
    binding = await sql_device_service(session).topic_binding(place_id)
    if binding is None:
        logger.info(
            "retire: home=%s/%s outcome=skipped reason=no device pin "
            "(Cloud machine released, or never ran on a device)",
            project_id,
            place_id,
        )
        return
    await _remove_home(
        session, project_id, place_id, binding.device_id, reason="archived"
    )


async def _remove_home(
    session: AsyncSession,
    project_id: uuid.UUID,
    place_id: uuid.UUID,
    device_id: str,
    *,
    reason: str,
) -> bool:
    """Store the home's transcripts on the platform, then delete it. Only a
    stored home — or one with nothing to store — is deleted; anything short of
    that leaves it in place for the sweep to try again."""
    from app.domain.agent.device_hub import DeviceOffline, device_hub
    from app.domain.agent.device_provider import (
        remove_device_home,
        upload_device_transcripts,
    )

    if not device_hub.is_online(device_id):
        logger.info(
            "retire: home=%s/%s device=%s outcome=left reason=device offline "
            "(the sweep will retry when it connects)",
            project_id,
            place_id,
            device_id,
        )
        return False
    # The upload authenticates as the device, with the credential the device
    # already holds; it is read here rather than off the machine because the
    # exec frame is the one place the platform can hand the command its env.
    device = await sql_device_service(session).get_device(device_id)
    if device is None:
        logger.warning(
            "retire: home=%s/%s device=%s outcome=left reason=device row gone, "
            "nothing to upload the transcripts as",
            project_id,
            place_id,
            device_id,
        )
        return False
    try:
        outcome, receipt = await upload_device_transcripts(
            device_id, project_id, place_id, token=device.token
        )
        if outcome == "uploaded" and not await _record_transcripts_archived(
            session, place_id
        ):
            outcome = "uploaded (no place row to record it on)"
        logger.info(
            "retire: home=%s/%s device=%s transcripts=%s%s",
            project_id,
            place_id,
            device_id,
            outcome,
            f" receipt={receipt}" if receipt else "",
        )
        await remove_device_home(device_id, project_id, place_id)
    except DeviceOffline:
        logger.info(
            "retire: home=%s/%s device=%s outcome=left reason=device went offline",
            project_id,
            place_id,
            device_id,
        )
        return False
    except (TimeoutError, RuntimeError) as exc:
        logger.warning(
            "retire: home=%s/%s device=%s outcome=left reason=%s",
            project_id,
            place_id,
            device_id,
            exc,
        )
        return False
    except Exception:  # noqa: BLE001 — one device must not stop an archive or a sweep
        logger.warning(
            "retire: home=%s/%s device=%s outcome=error",
            project_id,
            place_id,
            device_id,
            exc_info=True,
        )
        return False
    logger.info(
        "retire: home=%s/%s device=%s outcome=removed reason=%s",
        project_id,
        place_id,
        device_id,
        reason,
    )
    return True


async def _record_transcripts_archived(
    session: AsyncSession, place_id: uuid.UUID
) -> bool:
    """Stamp the place — a room, or a thread in one — with when its transcripts
    reached the platform. False when no row has this id (the home of a deleted
    place: its archive is on disk all the same, there is just nothing to note
    it on)."""
    now = datetime.now(UTC)
    if await TopicRepository(session).mark_transcripts_archived(place_id, now):
        return True
    return await TaskService(session).mark_transcripts_archived(place_id, now)


# ---- on a clock -------------------------------------------------------------


async def sweep_retired_storage(
    sessions: SessionFactory, *, retention_days: float | None = None
) -> dict[str, int]:
    """Remove the worktrees and device homes of places that are gone or have
    been archived longer than the retention. Returns what it did and what it
    could not do; what it deliberately kept is only in the log, one line each."""
    days = (
        settings.topic_home_retention_days if retention_days is None else retention_days
    )
    cutoff = datetime.now(UTC) - timedelta(days=days)
    counts = {
        "worktrees_removed": 0,
        "worktrees_left": 0,
        "homes_removed": 0,
        "homes_left": 0,
    }
    await _sweep_worktrees(sessions, cutoff, counts)
    await _sweep_homes(sessions, cutoff, counts)
    return counts


async def _sweep_worktrees(
    sessions: SessionFactory, cutoff: datetime, counts: dict[str, int]
) -> None:
    by_project: dict[uuid.UUID, list[tuple[str, Path]]] = {}
    for project_id, prefix, path in await asyncio.to_thread(ws.topic_worktrees_on_disk):
        by_project.setdefault(project_id, []).append((prefix, path))
    async with sessions() as session:
        for project_id, entries in by_project.items():
            owners = await _tree_owners(session, project_id)
            for prefix, path in entries:
                matched = {
                    oid: at for oid, at in owners.items() if oid.hex[:8] == prefix
                }
                remove, reason = _verdict(matched, cutoff)
                if not remove:
                    logger.info(
                        "sweep: worktree=%s outcome=kept reason=%s", path, reason
                    )
                    continue
                gone = await _retire_worktree(project_id, path, reason=reason)
                counts["worktrees_removed" if gone else "worktrees_left"] += 1


async def _tree_owners(
    session: AsyncSession, project_id: uuid.UUID
) -> dict[uuid.UUID, datetime | None]:
    """Everything a `topic_<hex8>` directory in this project can be named
    after, with when it was archived: a topic, or one of a room's trees (a
    later batch has its own id, and the tree is judged by its room). A tree
    whose room is gone resolves to nothing, like any other orphan."""
    topics = await TopicRepository(session).archival_for_project(project_id)
    owners = dict(topics)
    for tree in await WorkTreeService(session).trees_in_project(project_id):
        if tree.room_id in topics:
            owners.setdefault(tree.id, topics[tree.room_id])
    return owners


async def _sweep_homes(
    sessions: SessionFactory, cutoff: datetime, counts: dict[str, int]
) -> None:
    from app.domain.agent.device_hub import DeviceOffline, device_hub
    from app.domain.agent.device_provider import list_device_homes

    for device_id in sorted(device_hub.online_device_ids()):
        try:
            homes = await list_device_homes(device_id)
        except DeviceOffline:
            logger.info(
                "sweep: device=%s outcome=skipped reason=went offline", device_id
            )
            continue
        except Exception:  # noqa: BLE001 — one device must not stop the sweep
            logger.warning(
                "sweep: device=%s outcome=error listing homes", device_id, exc_info=True
            )
            continue
        async with sessions() as session:
            for project, place in homes:
                try:
                    project_id, place_id = uuid.UUID(project), uuid.UUID(place)
                except ValueError:
                    logger.info(
                        "sweep: home=%s/%s device=%s outcome=kept "
                        "reason=not a project/place id",
                        project,
                        place,
                        device_id,
                    )
                    continue
                remove, reason = _verdict(
                    await _place_archival(session, place_id), cutoff
                )
                if not remove:
                    logger.info(
                        "sweep: home=%s/%s device=%s outcome=kept reason=%s",
                        project_id,
                        place_id,
                        device_id,
                        reason,
                    )
                    continue
                gone = await _remove_home(
                    session, project_id, place_id, device_id, reason=reason
                )
                # Per home, not per device: a `transcripts_archived_at` stamp
                # must not be lost to a later home's failure on the same tick.
                await session.commit()
                counts["homes_removed" if gone else "homes_left"] += 1


async def _place_archival(
    session: AsyncSession, place_id: uuid.UUID
) -> dict[uuid.UUID, datetime | None]:
    """The place a home is named after — a room, or a thread in one — with when
    it ended; empty when nothing has that id. A closed thread without a
    `closed_at` reads as still open: with no date to judge by, keeping is the
    safe reading."""
    topic = await TopicRepository(session).get(place_id)
    if topic is not None:
        return {topic.id: topic.archived_at}
    task = await TaskService(session).get(place_id)
    if task is not None:
        return {task.id: task.closed_at if task.status == TaskStatus.closed else None}
    return {}


def _verdict(
    candidates: dict[uuid.UUID, datetime | None], cutoff: datetime
) -> tuple[bool, str]:
    """Whether a leftover named after `candidates` may go, and why either way."""
    if not candidates:
        return True, "no place with this id in the database"
    if len(candidates) > 1:
        return False, f"id prefix matches {len(candidates)} places, ambiguous"
    ((place_id, archived_at),) = candidates.items()
    if archived_at is None:
        return False, f"place {place_id} is active"
    if archived_at.tzinfo is None:
        archived_at = archived_at.replace(tzinfo=UTC)
    if archived_at >= cutoff:
        return (
            False,
            f"place {place_id} archived {archived_at:%Y-%m-%d}, within retention",
        )
    return True, f"place {place_id} archived {archived_at:%Y-%m-%d}, past retention"
