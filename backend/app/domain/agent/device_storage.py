"""What a machine holds under the platform's two storage roots, and what the
platform keeps of it before deleting it.

Read by room retirement (``topic.retire``) to find the homes and checkouts a
room left on each machine.

**Kept room files.** A room from before rooms had an executor worked in its
directory (``<home>/room``) directly, with no repository behind it, so what
its sessions left there is on no forge and in no snapshot. A room's cleanup
no longer refuses to delete a home over work that was not pushed, so before
one of those homes is deleted its room directory goes to the private bucket,
the conversation it belonged to is told until when, and the copy is deleted
after ``RETENTION``. During that time the platform's operators fetch it on
request with ``scripts/retained_files.py``.

Two moments send them. Every home on a self-hosted machine is looked at once,
when that machine connects (``keep_device_room_files``) and in a pass over
the connected ones that the room-cleanup sweep starts at most hourly
(``keep_room_files_due``; a machine that stays connected to the connection
owner across a deploy does not connect again), so the rooms are told before
anything archives them; each home is recorded in ``KeptRoomFiles``
whether or not it held anything. And a room's cleanup hands the machine an
upload URL with every removal (``room_files_upload_url``): the removal sends
what has not been sent yet before it deletes, in the same command, so nothing
of such a room is ever deleted without its copy (``record_room_files``).
"""

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import String, and_, cast, not_, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.sentences import say
from app.core.storage import private_storage
from app.domain.agent import resource_cleanup
from app.domain.agent.device_hub import DeviceHub, device_hub
from app.domain.agent.device_provider import DEVICE_HOME_ROOT, DEVICE_WORK_ROOT
from app.domain.topic.models import KeptRoomFiles


async def list_device_storage(
    device_id: str, *, hub: DeviceHub | None = None
) -> list[tuple[str, str, str]]:
    """Every ``(kind, project, place)`` under both device storage roots,
    as the device's shell sees them — names only, nothing resolved.

    Raises ``DeviceOffline`` like ``exec`` does; the caller decides what an
    unreachable device means for its sweep. Lists with a shell loop rather than
    `find -printf`, which is GNU-only and a device may be a Mac."""
    hub = hub or device_hub
    script = (
        f'for root in "{DEVICE_HOME_ROOT}" "{DEVICE_WORK_ROOT}"; do '
        '(cd "$root" 2>/dev/null || exit 0; '
        # A project/place symlink may point into the device owner's other data.
        'for p in */*; do if [ -d "$p" ] && '
        '[ ! -L "${p%%/*}" ] && [ ! -L "$p" ]; then '
        'printf "%s\\t%s\\n" "${root##*/}" "$p"; fi; done); done'
    )
    result = await hub.exec(device_id, ["sh", "-lc", script], timeout=30)
    if result.get("exit") != 0 or result.get("truncated"):
        # The machine's own words: without them a failed listing on a warm
        # host said nothing a fix could start from (three on 2026-10-06).
        raise RuntimeError(
            "device storage listing failed or was truncated: "
            f"exit={result.get('exit')} truncated={bool(result.get('truncated'))} "
            f"stderr={str(result.get('stderr') or '')[-300:]!r}"
        )
    pairs: list[tuple[str, str, str]] = []
    for line in str(result.get("stdout") or "").splitlines():
        kind, tab, path = line.partition("\t")
        project, sep, place = path.partition("/")
        if kind in {"home", "work"} and tab and sep and project and place:
            pairs.append((kind, project, place))
    return pairs


logger = logging.getLogger("cheesex.agent.device_storage")

#: How long a kept copy stays in the bucket.
RETENTION = timedelta(days=30)
#: Where in the private bucket the copies are.
KEY_PREFIX = "kept-room-files"
#: The largest room directory on dev was 1.2 GB: packing and sending it is
#: minutes, not the minute a cleanup action gets.
KEEP_ROOM_TIMEOUT_S = 1500.0
#: A claim on a home older than this was left by a process that went away
#: while its files were being sent; another may take the home over.
CLAIM_STALE = timedelta(seconds=2 * KEEP_ROOM_TIMEOUT_S)
#: How long the upload URL a machine is handed stays good.
URL_TTL_S = 2 * 3600


def key_for(project_id: uuid.UUID, resource_id: str) -> str:
    return f"{KEY_PREFIX}/{project_id}/{uuid.UUID(resource_id)}.tar.gz"


def _private_bucket():
    try:
        return private_storage()
    except RuntimeError:
        return None


async def _place(session, resource_id: str):
    """The conversation whose home this is: the room or task the id names,
    else the one whose session held a lease on it. None when nothing does."""
    from app.domain.agent_session.models import AgentSession
    from app.domain.room_task.place import PlaceResolver

    places = PlaceResolver(session)
    place = await places.conversation(uuid.UUID(resource_id))
    if place is not None:
        return place
    conversation = await session.scalar(
        select(AgentSession.conversation_id)
        .where(
            or_(
                cast(AgentSession.work_lease, String).contains(resource_id),
                cast(AgentSession.execution_request, String).contains(resource_id),
            )
        )
        .limit(1)
    )
    return None if conversation is None else await places.conversation(conversation)


async def _row(session, device_id, project_id, resource_id, **values):
    """Insert the home's row unless there is one, as one statement: two
    processes looking at the same home at once cannot both insert it. Answers
    whether this call inserted it."""
    inserted = await session.execute(
        insert(KeptRoomFiles)
        .values(
            project_id=project_id,
            device_id=device_id,
            resource_id=resource_id,
            **values,
        )
        .on_conflict_do_nothing(constraint="uq_kept_room_files_home")
        .returning(KeptRoomFiles.id)
    )
    return inserted.first() is not None


async def _claim(session, device_id, project_id, resource_id) -> bool:
    """Take the home for this process to look at: new to the table, or left
    mid-send by a process that went away (``CLAIM_STALE``). Every other process
    looking at it now gets False and leaves it alone."""
    now = datetime.now(UTC)
    claimed = (
        await _row(session, device_id, project_id, resource_id, looking_since=now)
        or (
            await session.execute(
                update(KeptRoomFiles)
                .where(
                    KeptRoomFiles.device_id == device_id,
                    KeptRoomFiles.resource_id == resource_id,
                    KeptRoomFiles.key.is_(None),
                    KeptRoomFiles.looking_since < now - CLAIM_STALE,
                )
                .values(looking_since=now)
                .returning(KeptRoomFiles.id)
            )
        ).first()
        is not None
    )
    await session.commit()
    return claimed


async def room_files_upload_url(
    session, device_id: str, project_id: uuid.UUID, resource_id: str, *, storage=None
) -> str:
    """What a removal of this home is handed as ``CHEESE_KEEP_URL``: where to
    send its room's files, ``KEPT_ALREADY`` when they were sent before, or
    nothing when there is no private bucket — then a home that has such files
    is not removed."""
    sent = await session.scalar(
        select(KeptRoomFiles.key).where(
            KeptRoomFiles.device_id == device_id,
            KeptRoomFiles.resource_id == resource_id,
            KeptRoomFiles.key.is_not(None),
        )
    )
    if sent is not None:
        return resource_cleanup.KEPT_ALREADY
    bucket = storage or _private_bucket()
    if bucket is None:
        return ""
    return await bucket.presign(
        key_for(project_id, resource_id), "put_object", URL_TTL_S
    )


async def record_room_files(
    session,
    device_id: str,
    project_id: uuid.UUID,
    resource_id: str,
    kept: dict | None,
    *,
    storage=None,
) -> None:
    """Record what the machine answered for one home — the size and MD5 of
    what it sent, or None — and tell its conversation, once. A copy the
    bucket does not hold as sent is recorded all the same, and said in the
    log: it is the only one there is."""
    await _row(session, device_id, project_id, resource_id)
    row = await session.scalar(
        select(KeptRoomFiles)
        .where(
            KeptRoomFiles.device_id == device_id,
            KeptRoomFiles.resource_id == resource_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert row is not None
    row.looking_since = None
    if kept is not None and row.key is None:
        key = key_for(project_id, resource_id)
        bucket = storage or _private_bucket()
        stored = await bucket.stat(key) if bucket is not None else None
        if stored != (kept["size"], kept["md5"]):
            logger.error(
                "kept room files differ: the bucket holds %s for %s, the machine "
                "sent %s",
                stored,
                key,
                (kept["size"], kept["md5"]),
            )
        place = await _place(session, resource_id)
        row.key = key
        row.size = kept["size"]
        row.expires_at = datetime.now(UTC) + RETENTION
        if place is not None:
            row.room_id, row.task_id = place.room_id, place.task_id
            await _tell(session, row)
    await session.commit()
    logger.info(
        "room files looked at device=%s resource=%s kept=%s",
        device_id,
        resource_id,
        row.key,
    )


async def keep_room_files(
    session,
    device_id: str,
    project_id: uuid.UUID,
    resource_id: str,
    *,
    hub=None,
    storage=None,
) -> None:
    """Look at one home once, without removing it: send its room's files to
    the bucket if it has any, and record it either way. A home another process
    is looking at is left to it (``_claim``)."""
    if not await _claim(session, device_id, project_id, resource_id):
        return
    url = await room_files_upload_url(
        session, device_id, project_id, resource_id, storage=storage
    )
    if url == resource_cleanup.KEPT_ALREADY:
        return
    # No transaction is held while the machine packs and sends.
    await session.commit()
    result = await (hub or device_hub).exec(
        device_id,
        ["python3", "-", "keep-room", str(project_id), resource_id, "-", "-"],
        stdin=Path(resource_cleanup.__file__).read_text(),
        env={"CHEESE_KEEP_URL": url},
        timeout=KEEP_ROOM_TIMEOUT_S,
    )
    if result.get("exit") != 0 or result.get("truncated"):
        raise RuntimeError(
            "keeping the room's files failed: "
            + str(result.get("stderr") or "no answer from the machine")[-1500:]
        )
    kept = json.loads(str(result["stdout"]).strip().splitlines()[-1])["kept"]
    await record_room_files(
        session, device_id, project_id, resource_id, kept, storage=storage
    )


async def _tell(session, row: KeptRoomFiles) -> None:
    """Say once in the conversation that its files are kept, until when, and
    how to get them back."""
    from app.domain.agent.announce import announce
    from app.domain.agent.platform_notices import (
        EVENT_ROOM_FILES_KEPT,
        SEVERITY_WARN,
        WHO_HUMAN,
        notice,
    )

    told = await session.scalar(
        select(KeptRoomFiles.id).where(
            KeptRoomFiles.room_id == row.room_id,
            KeptRoomFiles.task_id.is_(None)
            if row.task_id is None
            else KeptRoomFiles.task_id == row.task_id,
            KeptRoomFiles.key.is_not(None),
            KeptRoomFiles.id != row.id,
        )
    )
    if told is not None:
        return
    assert row.room_id is not None
    assert row.expires_at is not None
    await announce(
        session,
        place_id=row.room_id,
        task_id=row.task_id,
        content=say("roomFilesKept", date=row.expires_at.date().isoformat()),
        meta=notice(EVENT_ROOM_FILES_KEPT, severity=SEVERITY_WARN, who=WHO_HUMAN),
    )


async def keep_device_room_files(
    sessions, device_id: str, *, hub=None, storage=None
) -> int:
    """Look at every home on a self-hosted machine not looked at yet; answers
    how many were. One home that fails is tried again at the next connection
    and does not stop the others."""
    from app.domain.device.supply import Supply
    from app.domain.device.wiring import sql_device_service

    hub = hub or device_hub
    async with sessions() as session:
        device = await sql_device_service(session).get_device(device_id)
        if device is None or device.supply is not Supply.self_hosted:
            return 0
        # Every home recorded, but one whose send was left unfinished.
        known = set(
            await session.scalars(
                select(KeptRoomFiles.resource_id).where(
                    KeptRoomFiles.device_id == device_id,
                    not_(
                        and_(
                            KeptRoomFiles.key.is_(None),
                            KeptRoomFiles.looking_since
                            < datetime.now(UTC) - CLAIM_STALE,
                        )
                    ),
                )
            )
        )
        # A machine no session ever held a lease on has no room's home on it.
        from app.domain.agent_session.models import AgentSession

        worked_on = await session.scalar(
            select(AgentSession.id)
            .where(
                or_(
                    cast(AgentSession.work_lease, String).contains(device_id),
                    cast(AgentSession.execution_request, String).contains(device_id),
                )
            )
            .limit(1)
        )
        if worked_on is None:
            return 0
    try:
        listed = await list_device_storage(device_id, hub=hub)
    except Exception:  # noqa: BLE001 — its next connection looks again
        # WARNING: a machine that went again right after connecting is the
        # usual cause, and that is no fault anyone must answer.
        logger.warning("room files not looked at device=%s", device_id, exc_info=True)
        return 0
    homes = [
        (project, resource)
        for kind, project, resource in listed
        if kind == "home" and resource not in known
    ]
    looked = 0
    for project, resource in homes:
        try:
            project_id, _ = uuid.UUID(project), uuid.UUID(resource)
        except ValueError:
            continue
        async with sessions() as session:
            try:
                await keep_room_files(
                    session, device_id, project_id, resource, hub=hub, storage=storage
                )
                looked += 1
            except Exception:  # noqa: BLE001 — one home must not stop the others
                logger.warning(
                    "room files not looked at device=%s resource=%s",
                    device_id,
                    resource,
                    exc_info=True,
                )
    return looked


async def keep_room_files_everywhere(sessions, *, hub=None, storage=None) -> int:
    """``keep_device_room_files`` for every connected self-hosted machine; one
    that is not connected is looked at when it connects. Answers how many homes
    were looked at."""
    from app.domain.device.models import DeviceRow
    from app.domain.device.supply import Supply

    async with sessions() as session:
        device_ids = list(
            await session.scalars(
                select(DeviceRow.device_id).where(
                    DeviceRow.supply == Supply.self_hosted
                )
            )
        )
    hub = hub or device_hub
    looked = 0
    for device_id in device_ids:
        if hub.is_online(device_id):
            looked += await keep_device_room_files(
                sessions, device_id, hub=hub, storage=storage
            )
    return looked


#: How often the pass over every connected machine runs at most.
EVERYWHERE_EVERY = timedelta(hours=1)
# When this process last started that pass; None: not yet.
_everywhere_at: datetime | None = None


async def keep_room_files_due(sessions, *, hub=None) -> int:
    """``keep_room_files_everywhere``, when this process has not run it within
    ``EVERYWHERE_EVERY`` and knows of a connected machine. Not before: right
    after a start the backend has not yet heard which machines are connected,
    and a pass then would look at none and wait out the hour."""
    global _everywhere_at
    hub = hub or device_hub
    now = datetime.now(UTC)
    if _everywhere_at is not None and now - _everywhere_at < EVERYWHERE_EVERY:
        return 0
    if not hub.online_device_ids():
        return 0
    _everywhere_at = now
    return await keep_room_files_everywhere(sessions, hub=hub)


async def expire_room_files(sessions, *, storage=None) -> int:
    """Delete the copies whose time is up; answers how many."""
    now = datetime.now(UTC)
    async with sessions() as session:
        due = list(
            await session.scalars(
                select(KeptRoomFiles).where(
                    KeptRoomFiles.key.is_not(None),
                    KeptRoomFiles.deleted_at.is_(None),
                    KeptRoomFiles.expires_at <= now,
                )
            )
        )
        if not due:
            return 0
        bucket = storage or private_storage()
        for row in due:
            assert row.key is not None
            await bucket.delete(row.key)
            row.deleted_at = now
            await session.commit()
            logger.info("kept room files expired key=%s", row.key)
        return len(due)
