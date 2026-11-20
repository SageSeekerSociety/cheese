"""An environment's own rows: what it is, which conversation points to it,
and what its install left — read and written here, with no session in view.

Who is attached to an environment and what a session's agent is owed are the
attachment's (``agent_session.attachment``), which writes through here.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    String,
    delete,
    exists,
    func,
    literal_column,
    select,
    update,
)
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


def key(name: str) -> ColumnElement[str]:
    # Written into the statement, not bound: the keys never change, and bound
    # they were a dozen parameters on every statement that reads the dict.
    return literal_column(f"'{name}'", String)


def described(*attachment: Any) -> ColumnElement[Any]:
    """An environment as one dict, for a query over ``environments``: what
    its install left (``executor``), its machine and home, its status and the
    install running in it, with the ``attachment`` key/value pairs a reader
    acting through one of its attachments adds. The one construction of that
    dict."""
    return Environment.executor.op("||")(
        func.jsonb_strip_nulls(
            func.jsonb_build_object(
                key("kind"),
                key("device"),
                *attachment,
                key("device_id"),
                Environment.device_id,
                key("resource_id"),
                Environment.resource_id,
                key("room_resource_id"),
                Environment.room_resource_id,
                key("status"),
                Environment.status,
                key("claim"),
                Environment.claim,
                key("claim_until"),
                Environment.claim_until,
                key("verified_at"),
                Environment.verified_at,
            )
        )
    )


async def environment_of_conversation(
    db: AsyncSession, conversation_id: uuid.UUID
) -> Environment | None:
    """The environment the conversation points to: the place of its default
    choice, found from the conversation and never by picking one of its
    sessions. None while it points to none."""
    return await db.scalar(
        select(Environment)
        .join(
            ConversationEnvironment,
            ConversationEnvironment.environment_id == Environment.id,
        )
        .where(ConversationEnvironment.conversation_id == conversation_id)
        .execution_options(populate_existing=True)
    )


async def view(db: AsyncSession, environment_id: uuid.UUID) -> dict | None:
    """The environment as one dict (`described`), with no attachment's keys;
    None while it is on no machine."""
    return await db.scalar(
        select(described()).where(
            Environment.id == environment_id, Environment.device_id.is_not(None)
        )
    )


async def attachments_of(
    db: AsyncSession, environment_id: uuid.UUID
) -> list[EnvironmentAttachment]:
    """Who is attached to the environment, the one that acted last first."""
    return list(
        await db.scalars(
            select(EnvironmentAttachment)
            .where(EnvironmentAttachment.environment_id == environment_id)
            .order_by(
                EnvironmentAttachment.acted_at.desc(),
                EnvironmentAttachment.session_id,
            )
        )
    )


def holds_a_copy(environment: dict | None) -> bool:
    """Whether an environment (`view`) has taken its machine: it is ready, or
    its install got far enough to record where its executor is. One not
    ready that records nothing is a reservation whose executor is still
    being installed, or whose install failed and waits for the next call to
    retry: there is nothing on the machine to ask. One that records where
    its executor is holds the machine's copy, which may carry work nobody
    has pushed, and is read."""
    return environment is not None and bool(
        environment.get("status", READY) == READY
        or environment.get("state")
        or environment.get("home")
    )


async def working_copy(
    db: AsyncSession, conversation_id: uuid.UUID, generation: str
) -> tuple[dict, bool] | None:
    """The environment holding this task's (or 支线's) working copy on the
    room's current ``generation``: the one the conversation points to, as
    `view` reads it, and whether nothing on it answers for that copy now —
    its sandbox was destroyed or parked and nothing has placed it again since
    (``RELEASED``). Whether an agent attached to it has been told is another
    fact, and does not enter into this. None while it points to none that
    holds a copy (`holds_a_copy`) of this generation."""
    environment = await environment_of_conversation(db, conversation_id)
    if environment is None or environment.room_resource_id != generation:
        return None
    found = await view(db, environment.id)
    if found is None or not holds_a_copy(found):
        return None
    return found, found.get("status") == RELEASED


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
