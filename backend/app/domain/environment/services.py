"""An environment's own rows: what it is, which conversation points to it,
and what its install left — read and written here, with no session in view.

Who is attached to an environment and what a session's agent is owed are the
attachment's (``agent_session.attachment``), which writes through here.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.conversation.services import of_room
from app.domain.environment.models import (
    READY,
    RELEASED,
    ConversationEnvironment,
    Environment,
    EnvironmentAttachment,
)

#: The keys of an environment's dict that are columns of the environment;
#: every other key but the attachment's is what its install left
#: (``Environment.executor``).
_COLUMNS = ("device_id", "resource_id", "room_resource_id", "status")
_MOMENTS = ("claim_until", "verified_at")
_ATTACHMENT_KEYS = ("generation", "setup_digest")
_DERIVED = ("kind", "session_id", "claim")


async def environments_held(
    db: AsyncSession,
    *,
    room_id: uuid.UUID | None = None,
    device_id: str | None = None,
) -> list[Environment]:
    """Every environment of ``room_id``'s conversations, or on ``device_id``:
    those sessions are attached to and those they left on people's machines,
    whose directories a cleanup still removes."""
    query = select(Environment)
    if room_id is not None:
        query = query.where(of_room(Environment.topic_id, room_id))
    if device_id is not None:
        query = query.where(Environment.device_id == device_id)
    return list(await db.scalars(query))


@dataclass(frozen=True, slots=True)
class Where:
    """What a new environment is made as: the conversation whose machine it
    is (its holder), its project, the holder's generation it belongs to, and
    the choice it is for."""

    topic_id: uuid.UUID
    project_id: uuid.UUID | None
    generation: str
    choice: dict


def _moment(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def write(environment: Environment, described: dict) -> None:
    environment.device_id = described["device_id"]
    environment.resource_id = described["resource_id"]
    environment.room_resource_id = described["room_resource_id"]
    environment.status = described.get("status", READY)
    environment.claim = described.get("claim")
    environment.claim_until = _moment(described.get("claim_until"))
    environment.verified_at = _moment(described.get("verified_at"))
    environment.executor = {
        key: value
        for key, value in described.items()
        if key not in (*_COLUMNS, *_MOMENTS, *_ATTACHMENT_KEYS, *_DERIVED)
    }


def new_environment(db: AsyncSession, where: Where) -> Environment:
    environment = Environment(
        id=uuid.uuid4(),
        topic_id=where.topic_id,
        project_id=where.project_id,
        choice=where.choice,
        device_id=None,
        # Its own directory: one environment, one home on its machine.
        resource_id=str(uuid.uuid4()),
        room_resource_id=where.generation,
        executor={},
    )
    db.add(environment)
    return environment


def is_of(environment: Environment, choice: dict, where: Where) -> bool:
    """Whether ``environment`` is one of ``choice`` for the holder's
    generation now: one made for an earlier generation of the room (before
    an unarchive) is not the room's any more."""
    return (
        environment.choice == choice
        and environment.room_resource_id == where.generation
    )


def on_a_persons(environment: Environment) -> bool:
    """Whether the environment is on a person's machine, where its
    directories stay until the cleanup removes them."""
    return environment.device_id is not None and not environment.executor.get(
        "platform"
    )


async def pointer(
    db: AsyncSession, conversation_id: uuid.UUID
) -> ConversationEnvironment:
    """The conversation's pointer, made if it had none, locked: two sessions
    of one conversation settling at once agree on one environment."""
    await db.execute(
        insert(ConversationEnvironment)
        .values(conversation_id=conversation_id)
        .on_conflict_do_nothing(
            index_elements=[ConversationEnvironment.conversation_id]
        )
    )
    pointer = await db.scalar(
        select(ConversationEnvironment)
        .where(ConversationEnvironment.conversation_id == conversation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert pointer is not None  # inserted above
    return pointer


async def forget_if_unused(db: AsyncSession, environment_id: uuid.UUID) -> None:
    """Delete the environment when nobody is attached to it and no
    conversation points to it: nothing can find it again. What it named on
    the platform — its sandbox — is the idle sweep's from here, as any
    sandbox nobody uses."""
    used = await db.scalar(
        select(
            exists().where(EnvironmentAttachment.environment_id == environment_id)
            | exists().where(ConversationEnvironment.environment_id == environment_id)
        )
    )
    if not used:
        await db.execute(delete(Environment).where(Environment.id == environment_id))
    await db.flush()


async def release(
    db: AsyncSession, environment_id: uuid.UUID, device_id: str | None
) -> None:
    """The environment, if on ``device_id``, is released: its sandbox there
    was destroyed, parked or given up, it is checked by nobody since, and the
    claim of an install that was running on it ended with it. Whoever is
    attached stays attached; their next call places it again."""
    environment = await db.get(Environment, environment_id, populate_existing=True)
    if environment is not None and environment.device_id == device_id:
        environment.status = RELEASED
        environment.verified_at = None
        environment.claim = None
        environment.claim_until = None
        await db.flush()


async def unchecked(db: AsyncSession, environment_id: uuid.UUID) -> None:
    """The environment's executor is to be checked whole before it is handed
    out again (``verified_at`` dropped)."""
    await db.execute(
        update(Environment)
        .where(Environment.id == environment_id, Environment.verified_at.is_not(None))
        .values(verified_at=None)
        .execution_options(synchronize_session="fetch")
    )


async def distrust(
    db: AsyncSession, session_id: uuid.UUID, generation: str | None
) -> None:
    """A call on the session's environment failed on the machine's side: the
    next request for it checks it whole. Only what says the environment was
    checked is written, and only while the session is attached under
    ``generation`` — the execution route calls this, served by the device
    connection owner."""
    await db.execute(
        update(Environment)
        .where(
            Environment.id
            == select(EnvironmentAttachment.environment_id)
            .where(
                EnvironmentAttachment.session_id == session_id,
                EnvironmentAttachment.generation == generation,
            )
            .scalar_subquery(),
            Environment.verified_at.is_not(None),
        )
        .values(verified_at=None)
    )
