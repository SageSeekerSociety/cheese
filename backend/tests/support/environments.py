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
from app.domain.machine import holder as holders
from app.domain.machine.models import (
    PREPARING,
    READY,
    SELF_HOSTED_SPEC,
    ConversationEnvironment,
    Environment,
    EnvironmentAttachment,
)

#: The lease keys that describe the executor installed in the environment.
EXECUTOR_KEYS = (
    "state",
    "release",
    "upgrade_pending",
    "desired_release",
    "workspace",
    "mcp_servers",
    "url",
    "environment_status",
    "root_snapshot_notice",
)


async def give_environment(
    db: AsyncSession,
    session: AgentSession,
    lease: dict,
    *,
    host_id: uuid.UUID | None = None,
    spec: str | None = None,
    pointed: bool = True,
    environment: Environment | None = None,
) -> EnvironmentAttachment:
    """Attach ``session`` to the environment ``lease`` describes, creating it
    unless ``environment`` is one already there. Flushes; the caller commits.

    ``host_id`` places it on a platform host rather than on the lease's
    device; ``spec`` defaults to a self-hosted machine on a device and to the
    standard sandbox on a host."""
    if environment is None:
        holder_id = await holders.holder_of(db, session.conversation_id)
        holder = await holders.look(db, holder_id)
        resource = lease.get("resource_id") or str(uuid.uuid4())
        claim_until = lease.get("claim_until")
        environment = Environment(
            host_id=host_id,
            device_id=None if host_id is not None else lease["device_id"],
            spec=spec or ("standard" if host_id is not None else SELF_HOSTED_SPEC),
            project_id=holder.project_id if holder is not None else None,
            topic_id=holder_id,
            room_resource_id=lease.get("room_resource_id")
            or (holder.generation if holder is not None else resource),
            resource_id=resource,
            executor={key: lease[key] for key in EXECUTOR_KEYS if key in lease},
            status=READY if lease.get("status", "ready") == "ready" else PREPARING,
            claim=lease.get("claim"),
            claim_until=(
                datetime.fromisoformat(claim_until)
                if isinstance(claim_until, str)
                else claim_until
            ),
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
