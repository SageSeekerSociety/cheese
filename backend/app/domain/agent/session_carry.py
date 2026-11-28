"""Carry a conversation to the computer its session now starts on.

When a session starts away from its conversation (`session_move.host_move`),
the channel asks this module to bring the conversation along before it records
the new place. The computer it was on packs that conversation's files and sends
them to the private bucket through a presigned URL; the bucket is checked to
hold them as sent; the computer it starts on fetches them, checks every file
against the manifest, and lays them into its room home (`carry_files`). The
start then resumes the conversation as it would have on the old computer:
`release.carry_conversation` joins pieces written under other working
directories once the executor client starts.

The bytes never pass through the backend or an exec's output, which the
connector caps at 1 MiB per stream. The bucket object is kept: it is a trace
of the agent's work, and every agent trace is kept (#4398).

Whatever stops the carry ends in the same place: the conversation is ended and
the room is told why (`session_move.end_moved`). The turn never waits for a
computer that is away: one that is not connected now is a new conversation now.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.storage import private_storage
from app.domain.agent import carry_files
from app.domain.agent.harness import SessionRef
from app.domain.agent.place import seat_key, seat_name
from app.domain.device.models import DeviceRow
from app.domain.session_record import service as session_records
from app.domain.user.services import handles_by_ids

logger = logging.getLogger(__name__)

#: The most a conversation may hold, all its files together, to be carried.
#: Until the sizes conversations reach are measured, a bound that keeps the
#: first turn after a move within a minute on a member's uplink.
CARRY_MAX_BYTES = 256 * 1024 * 1024
#: How long the whole carry may hold the turn's start: past it the session
#: starts a new conversation instead.
CARRY_TIMEOUT_S = 300.0
#: How long the URLs a machine is handed stay good.
URL_TTL_S = 3600
#: Where in the private bucket carried conversations are kept, for good.
KEY_PREFIX = "carry"

#: Why a conversation was not carried, as `session_move` words it.
OFFLINE = "offline"
TOO_LARGE = "too_large"
NOT_ALLOWED = "not_allowed"
FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Where:
    """One end of a carry: a session host and the room generation there."""

    device: str
    resource: str


async def may_carry(
    db: AsyncSession, session: SessionRef, token: str, *, target: str
) -> bool:
    """Whether conversation ``token`` may be carried onto ``target`` without
    anyone reading what is not theirs.

    The deployment's session host is trusted with every member's content, kept
    to its own seat: anything may be carried onto it. A member's own computer
    is read by its owner, so a conversation goes there only when every piece
    of private content the runner stamped in it is that owner's (the rule
    `turn_speakers` holds a turn to). The stamps are the platform's own record
    of whose content entered the conversation (`session_record.privacy`)."""
    if target == settings.agent_session_device_id:
        return True
    row = await db.get(DeviceRow, target)
    if row is None or row.owner_user_id is None:
        return False
    owner = set(await handles_by_ids(db, [row.owner_user_id]))
    if not owner:
        return False
    stamped = await session_records.private_owners(db, session.conversation_id, token)
    return stamped <= owner


async def carry(
    hub,
    session: SessionRef,
    token: str,
    *,
    origin: Where,
    target: Where,
    agent: str,
) -> str | None:
    """Bring conversation ``token`` from ``origin`` to ``target``: None once
    it is there, else why it is not (``OFFLINE``, ``TOO_LARGE``, ``FAILED``).
    ``agent`` is whose seat wrote it: a process still running there refuses."""
    if not hub.is_online(origin.device):
        return OFFLINE
    try:
        return await asyncio.wait_for(
            _carry(hub, session, token, origin=origin, target=target, agent=agent),
            CARRY_TIMEOUT_S,
        )
    except Exception:  # every failure is the same new conversation, and logged
        logger.exception(
            "session_carry_failed topic=%s seat=%s", session.conversation_id, agent
        )
        return FAILED


async def _carry(
    hub, session: SessionRef, token: str, *, origin: Where, target: Where, agent: str
) -> str | None:
    bucket = private_storage()
    key = (
        f"{KEY_PREFIX}/{session.project_id}/{session.conversation_id}/"
        f"{session.agent_handle}/{token}/{uuid.uuid4()}.tar.gz"
    )
    packed = await _run(
        hub,
        origin.device,
        {
            "action": "pack",
            "project": str(session.project_id),
            "resource": origin.resource,
            "room": str(session.topic_id),
            "seat": seat_name(seat_key(agent, session.inner_id)),
            "sid": token,
            "max_bytes": CARRY_MAX_BYTES,
            "url": await bucket.presign(key, "put_object", URL_TTL_S),
        },
    )
    if packed.get("too_large"):
        logger.info(
            "session_carry_too_large topic=%s seat=%s bytes=%s",
            session.conversation_id,
            agent,
            packed["too_large"],
        )
        return TOO_LARGE
    if "manifest" not in packed:
        # Still being written by a process of the seat, or not on that
        # computer at all.
        logger.info(
            "session_carry_refused topic=%s seat=%s answer=%s",
            session.conversation_id,
            agent,
            packed,
        )
        return FAILED
    sent = packed["sent"]
    if await bucket.stat(key) != (sent["size"], sent["md5"]):
        # i18n-exempt: a carry's reason, for the log; the room is told in its own words
        raise RuntimeError(f"the bucket does not hold {key} as it was sent")
    await _run(
        hub,
        target.device,
        {
            "action": "unpack",
            "project": str(session.project_id),
            "resource": target.resource,
            "manifest": packed["manifest"],
            "url": await bucket.presign(key, "get_object", URL_TTL_S),
        },
    )
    logger.info(
        "session_carried topic=%s seat=%s files=%d bytes=%d key=%s",
        session.conversation_id,
        agent,
        len(packed["manifest"]),
        sent["size"],
        key,
    )
    return None


async def _run(hub, device: str, payload: dict) -> dict:
    """Run ``carry_files`` on ``device`` with ``payload``; its answer."""
    program = Path(carry_files.__file__).read_text()
    result = await hub.exec(
        device,
        ["python3", "-"],
        stdin=f"{program}\nmain(json.loads({json.dumps(payload)!r}))\n",
        timeout=CARRY_TIMEOUT_S,
    )
    if result.get("exit") != 0 or result.get("truncated"):
        # i18n-exempt: a carry's reason, for the log; the room is told in its own words
        raise RuntimeError(
            f"carry {payload['action']} on {device} failed: "
            f"{str(result.get('stderr') or '')[-1500:]}"
        )
    lines = str(result.get("stdout") or "").strip().splitlines()
    return json.loads(lines[-1]) if lines else {}
