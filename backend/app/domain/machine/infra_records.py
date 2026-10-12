"""What the cloud pool did to sessions that nobody else keeps a count of.

Two failures of the pool leave no record of their own: a session refused a
sandbox because the pool is full or the provider keeps failing, and a host
given up on with sandboxes on it, whose row goes once the provider has deleted
the machine. Each is kept here as a run record that belongs to no
conversation: the room already hears about it in its own words (the refusal as
the tool call's answer, the lost sandbox as ``progress.tell_lost``), and these
are for the admin machines page (``admin.py``) and the run-records page.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.domain.conversation.models import Conversation
from app.domain.machine.models import CloudHost, MachineStatus
from app.domain.run_record.models import RunRecord
from app.domain.run_record.repository import RunRecordRepository

REFUSED = "cloud_refused"
HOST_LOST = "cloud_host_lost"

#: A refused session asks again on every tool call; one record per session in
#: this long says it was refused without counting each retry.
REFUSAL_REPEAT = timedelta(minutes=10)


async def keep_refusal(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    conversation_id: uuid.UUID,
    reason: str,
    said: str,
) -> None:
    """Keep that the session was refused a sandbox (``reason``: ``pool_full``
    or ``provider_failing``); ``said`` is what the session was told, its
    sentence key kept with it. Writes the caller's transaction."""
    seen = await session.scalar(
        select(RunRecord.id)
        .where(
            RunRecord.kind == REFUSED,
            RunRecord.created_at >= datetime.now(UTC) - REFUSAL_REPEAT,
            RunRecord.meta["session_id"].as_string() == str(session_id),
        )
        .limit(1)
    )
    if seen is not None:
        return
    project_id = await session.scalar(
        select(Conversation.project_id).where(Conversation.id == conversation_id)
    )
    await RunRecordRepository(session).add(
        project_id=project_id,
        conversation_id=None,
        content=said,
        meta={
            "event_type": REFUSED,
            "severity": "warn",
            "reason": reason,
            "session_id": str(session_id),
        },
    )


def lost_reason(host: CloudHost) -> str:
    """Why the pool gave up on a host: the provider reported it in error, it
    never became a device, or its connector stopped answering."""
    if host.status == MachineStatus.error:
        return "provider_error"
    if host.enrolled_at is None:
        return "never_enrolled"
    return "connector_away"


async def keep_host_lost(
    session: AsyncSession, host: CloudHost, *, lost: int, replaced: int
) -> None:
    """Keep that the pool gave up on ``host``, with how many working sandboxes
    went with it (``lost``) and how many being prepared were placed again
    (``replaced``). Writes the caller's transaction."""
    await RunRecordRepository(session).add(
        project_id=None,
        conversation_id=None,
        content=say(
            "cloudHostGivenUp", host=host.hostname, lost=lost, replaced=replaced
        ),
        meta={
            "event_type": HOST_LOST,
            "severity": "warn",
            "reason": lost_reason(host),
            "host": host.hostname,
            "machine_id": host.machine_id,
            "lost": lost,
            "replaced": replaced,
        },
    )
