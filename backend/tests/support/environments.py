"""Give a session in a test an execution environment, as placement leaves one.

Tests used to write a lease dict straight onto a session row; this takes the
same dict and stores what it describes the way the platform does: an
environment on the lease's machine, the conversation pointing to it (unless
``pointed=False``), and the session attached to it under the lease's
generation.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import AgentSession
from app.domain.environment.models import (
    READY,
    ConversationEnvironment,
    Environment,
    EnvironmentAttachment,
)
from app.domain.machine import holder as holders

#: The lease keys that are neither a column of the environment nor of the
#: attachment: what the install left (``Environment.executor``).
EXECUTOR_KEYS = (
    "platform",
    "home",
    "state",
    "release",
    "upgrade_pending",
    "desired_release",
    "workspace",
    "mcp_servers",
    "own",
    "url",
    "environment_status",
    "root_snapshot_notice",
)


def _moment(value) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else value


async def give_environment(
    db: AsyncSession,
    session: AgentSession,
    lease: dict,
    *,
    host_id: uuid.UUID | None = None,
    sandbox_home_id: uuid.UUID | None = None,
    pointed: bool = True,
    environment: Environment | None = None,
) -> EnvironmentAttachment:
    """Attach ``session`` to the environment ``lease`` describes, creating it
    unless ``environment`` is one already there. Flushes; the caller commits.

    ``host_id`` places it on a platform host, ``sandbox_home_id`` names the
    sandbox it is; a lease with ``verified_at`` reads as checked a moment
    ago."""
    if environment is None:
        holder_id = await holders.holder_of(db, session.conversation_id)
        holder = await holders.look(db, holder_id)
        resource = lease.get("resource_id") or str(uuid.uuid4())
        environment = Environment(
            topic_id=holder_id,
            project_id=holder.project_id if holder is not None else None,
            device_id=lease["device_id"],
            host_id=host_id,
            sandbox_home_id=sandbox_home_id,
            resource_id=resource,
            room_resource_id=lease.get("room_resource_id")
            or (holder.generation if holder is not None else resource),
            executor={key: lease[key] for key in EXECUTOR_KEYS if key in lease},
            status=lease.get("status", READY),
            claim=lease.get("claim"),
            claim_until=_moment(lease.get("claim_until")),
            verified_at=(
                datetime.now(UTC) - timedelta(seconds=1)
                if lease.get("verified_at")
                else None
            ),
        )
        db.add(environment)
        await db.flush()
    if pointed:
        pointer = await db.get(ConversationEnvironment, session.conversation_id)
        if pointer is None:
            db.add(
                ConversationEnvironment(
                    conversation_id=session.conversation_id,
                    environment_id=environment.id,
                )
            )
        else:
            pointer.environment_id = environment.id
    attachment = EnvironmentAttachment(
        id=session.id,
        environment_id=environment.id,
        session_id=session.id,
        generation=lease.get("generation") or str(uuid.uuid4()),
        setup_digest=lease.get("setup_digest"),
    )
    db.add(attachment)
    await db.flush()
    return attachment
