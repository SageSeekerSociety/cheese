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

from app.domain.agent_session import attachment as attachments
from app.domain.agent_session.models import AgentSession
from app.domain.agent_session.services import AgentSessionService
from app.domain.environment import services as environment_rows
from app.domain.environment.models import (
    READY,
    ConversationEnvironment,
    Environment,
    EnvironmentAttachment,
)
from app.domain.machine import holder as holders
from app.domain.machine.repositories import CloudHostRepository
from app.domain.topic.models import Topic

#: The lease keys that are a column of the environment or of the attachment,
#: or said by the row itself; every other key is what the install left
#: (``Environment.executor``).
NOT_EXECUTOR = (
    "kind",
    "session_id",
    "generation",
    "setup_digest",
    "device_id",
    "resource_id",
    "room_resource_id",
    "status",
    "claim",
    "claim_until",
    "verified_at",
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
    choice: dict | None = None,
) -> EnvironmentAttachment:
    """Attach ``session`` to the environment ``lease`` describes, creating it
    unless ``environment`` is one already there. Flushes; the caller commits.

    ``host_id`` places it on a platform host, ``sandbox_home_id`` names the
    sandbox it is; a lease with ``verified_at`` reads as checked a moment
    ago."""
    if environment is None:
        holder_id = await holders.holder_of(db, session.conversation_id)
        holder = await holders.look(db, holder_id)
        # A lease that names no home of its own is in its room's, which an
        # archived room still names.
        room = await db.get(Topic, holder_id) if holder is None else None
        generation = (
            holder.generation
            if holder is not None
            else str(room.resource_id or room.id)
            if room is not None
            else str(uuid.uuid4())
        )
        resource = lease.get("resource_id") or generation
        environment = Environment(
            topic_id=holder_id,
            project_id=holder.project_id if holder is not None else None,
            device_id=lease.get("device_id", ""),
            host_id=host_id,
            sandbox_home_id=sandbox_home_id,
            resource_id=resource,
            choice=choice or await _choice_of(db, session, holder, lease),
            room_resource_id=lease.get("room_resource_id") or generation,
            executor={k: v for k, v in lease.items() if k not in NOT_EXECUTOR},
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


async def _choice_of(db: AsyncSession, session: AgentSession, holder, lease) -> dict:
    """The choice an environment given to ``session`` is of: the session's
    own, as its next call works it out, so that call finds this one."""
    if holder is not None:
        return (await holder.environment(db, session)).model_dump()
    if lease.get("own"):
        return {"kind": "sandbox", "spec": "standard"}
    return {"kind": "self_hosted", "device_id": lease.get("device_id", "")}


async def unplaced_environment(
    db: AsyncSession, session: AgentSession, *, pointed: bool = False
) -> uuid.UUID:
    """The environment ``session`` is attached to, made (on no machine yet,
    of the session's own choice) and attached if it has none: what a test of
    the pool places. ``pointed``: its choice is the conversation's default;
    otherwise it is its own, which the conversation points to only while it
    points to nothing usable. Flushes; the caller commits."""
    held = await attachments.attachment_of(db, session.id)
    if held is not None and held.environment_id is not None:
        return held.environment_id
    holder_id = await holders.holder_of(db, session.conversation_id)
    holder = await holders.look(db, holder_id)
    assert holder is not None
    choice = (await holder.environment(db, session)).model_dump()
    environment = await attachments.settle(
        db,
        session,
        choice=choice,
        default=choice,
        own=not pointed,
        where=environment_rows.Where(
            topic_id=holder_id,
            project_id=holder.project_id,
            generation=holder.generation,
            choice=choice,
        ),
    )
    return environment.id


async def environment_of(db: AsyncSession, session_id: uuid.UUID) -> dict | None:
    """The environment a session's next call works in, as the platform reads
    it (`attachment.attachment_of`); None while it is attached to none."""
    held = await attachments.attachment_of(db, session_id)
    return held.environment if held is not None else None


async def change_environment(
    db: AsyncSession, session_id: uuid.UUID, lease: dict
) -> None:
    """Make the environment a session is attached to read as ``lease`` (a
    test standing in for what an install or a sweep would have left there).
    A lease that does not say which directory it is keeps the
    environment's. Flushes; the caller commits."""
    held = await attachments.attachment_of(db, session_id)
    if held is not None and held.environment_id is not None:
        current = await db.get(Environment, held.environment_id)
        assert current is not None
        lease = {
            "resource_id": current.resource_id,
            "room_resource_id": current.room_resource_id,
            **lease,
        }
    await attachments.attach(db, session_id, lease)


async def remember_place(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    agent_handle: str,
    harness: str,
    runtime_location: dict,
    environment: dict | None = None,
) -> None:
    """Record where a session's process runs and, with ``environment``, the
    environment it is attached to, as a placement leaves them."""
    sessions = AgentSessionService(db)
    await sessions.remember_place(
        conversation_id=conversation_id,
        agent_handle=agent_handle,
        harness=harness,
        runtime_location=runtime_location,
    )
    if environment is None:
        return
    row = await sessions.ensure(conversation_id, agent_handle, harness=harness)
    await attachments.unattach(db, row.id)
    await give_environment(db, row, environment)


async def attach_anew(db: AsyncSession, session: AgentSession, lease: dict, **given):
    """Attach ``session`` to a new environment ``lease`` describes, leaving
    the one it was attached to (`give_environment` takes ``given``)."""
    await attachments.unattach(db, session.id)
    return await give_environment(db, session, lease, **given)


async def works_in(
    db: AsyncSession, session_id: uuid.UUID, home, *, pointed: bool = False
) -> uuid.UUID:
    """Make the sandbox ``home`` (a ``SandboxHome`` just added) the one the
    session's environment names, as a placement leaves it; an environment
    of its own is made for a session attached to none, which its
    conversation points to when ``pointed``. Flushes; the caller commits.
    Answers the environment."""
    await db.flush()
    held = await attachments.attachment_of(db, session_id)
    if held is not None and held.environment_id is not None:
        environment_id = held.environment_id
    else:
        row = await db.get(AgentSession, session_id)
        assert row is not None
        environment_id = await unplaced_environment(db, row, pointed=pointed)
    await CloudHostRepository(db).name_home(environment_id, home)
    await db.flush()
    return environment_id
