"""Which execution environment a session is attached to, and what its agent is
owed about it: the one place that reads or changes either.

Everything that asks which machine and home a session works in, who is
attached to an environment, or what notice an agent is owed goes through the
functions here, and nothing else touches where that is stored. A caller gets
an `Attachment` (what the session's attachment and the environment it is on
say now) and changes it only by naming what happened: it attached, it
detached, the environment was released under it, its sandbox was lost or
parked.

The environment and the attachment are rows of their own
(``app.domain.environment``); what an agent is owed is its session's
``environment_notice``. A caller still reads an environment as one dict — its
machine, home, executor and status with the attachment's generation — built
in one place, `environment_column`.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    String,
    cast,
    delete,
    exists,
    func,
    literal_column,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import AgentSession, SessionPlace
from app.domain.conversation.services import of_room
from app.domain.environment.models import (
    READY,
    RELEASED,
    ConversationEnvironment,
    Environment,
    EnvironmentAttachment,
)

#: Re-exported: an environment whose sandbox is gone (`Attachment.released`).
__all__ = ["RELEASED"]

# What an agent is owed, as its session's ``environment_notice`` keeps it.
_LOST = "lost"
_LOST_PROCESSES = "lost_processes"
_PARKED = "parked"

#: The keys of an environment's dict that are columns of the environment;
#: every other key but the attachment's is what its install left
#: (``Environment.executor``).
_COLUMNS = ("device_id", "resource_id", "room_resource_id", "status")
_MOMENTS = ("claim_until", "verified_at")
_ATTACHMENT_KEYS = ("generation", "setup_digest")
_DERIVED = ("kind", "session_id", "claim")


@dataclass(frozen=True, slots=True)
class Notice:
    """What a session's agent is owed about the environment it works in and
    has not been told."""

    #: Its sandbox was destroyed (idle, or given up with its host).
    lost: bool = False
    #: What was running in the sandbox it lost, or in the one that was parked
    #: (each process's command line, its executor and bare shells left out,
    #: secrets masked). Empty when nothing was, or the host could not say.
    stopped: tuple[str, ...] = ()
    #: Its sandbox was parked: its processes stopped, its home kept.
    parked: bool = False

    @property
    def owed(self) -> bool:
        return self.lost or self.parked


def _notice(stored: dict | None) -> Notice:
    stored = stored or {}
    lost = bool(stored.get(_LOST))
    parked = stored.get(_PARKED) is not None
    stopped = stored.get(_LOST_PROCESSES if lost else _PARKED) or []
    return Notice(
        lost=lost, stopped=tuple(str(one) for one in stopped if one), parked=parked
    )


@dataclass(frozen=True, slots=True)
class Attachment:
    """A session's attachment and the environment it is on, as they stand."""

    #: The environment it is attached to: which machine and home, and how far
    #: its executor is installed there. None while it is attached to none.
    environment: dict | None
    #: What its agent is owed about it.
    notice: Notice

    @property
    def generation(self) -> str | None:
        """This attachment's generation: what a credential minted for it
        names, and what an install checks it is still installing for."""
        return (self.environment or {}).get("generation")

    @property
    def released(self) -> bool:
        """Its environment's sandbox was destroyed or parked since it attached:
        it names where the machine was, and nothing there answers."""
        return bool(self.environment) and self.environment.get("status") == RELEASED

    @property
    def device_id(self) -> str | None:
        return (self.environment or {}).get("device_id")


def _key(name: str) -> ColumnElement[str]:
    # Written into the statement, not bound: the keys never change, and bound
    # they were a dozen parameters on every statement that reads the dict.
    return literal_column(f"'{name}'", String)


def environment_column() -> ColumnElement[Any]:
    """The environment a session is attached to, as one dict labelled
    ``environment`` (None while it is attached to none), for a query over
    ``agent_sessions``: what its install left, its machine and home, its
    status and the install running in it, and the attachment's generation.
    The one construction of that dict."""
    described = func.jsonb_strip_nulls(
        func.jsonb_build_object(
            _key("kind"),
            _key("device"),
            _key("session_id"),
            cast(EnvironmentAttachment.session_id, String),
            _key("generation"),
            EnvironmentAttachment.generation,
            _key("setup_digest"),
            EnvironmentAttachment.setup_digest,
            _key("device_id"),
            Environment.device_id,
            _key("resource_id"),
            Environment.resource_id,
            _key("room_resource_id"),
            Environment.room_resource_id,
            _key("status"),
            Environment.status,
            _key("claim"),
            Environment.claim,
            _key("claim_until"),
            Environment.claim_until,
            _key("verified_at"),
            Environment.verified_at,
        )
    )
    return (
        select(Environment.executor.op("||")(described))
        .join(
            EnvironmentAttachment,
            EnvironmentAttachment.environment_id == Environment.id,
        )
        .where(EnvironmentAttachment.session_id == AgentSession.id)
        .correlate(AgentSession)
        .scalar_subquery()
        .label("environment")
    )


#: The columns `attachment` reads, for a query that selects them instead of
#: the whole row.
def attachment_columns() -> tuple[Any, ...]:
    return (environment_column(), AgentSession.environment_notice)


def attachment(row: Any) -> Attachment:
    """The attachment a row of `attachment_columns` describes. The one
    construction of an `Attachment`."""
    return Attachment(
        environment=row.environment, notice=_notice(row.environment_notice)
    )


async def attachment_of(
    db: AsyncSession, session_id: uuid.UUID, *, lock: bool = False
) -> Attachment | None:
    """This session's attachment as committed now; None when there is no such
    session. With ``lock`` the environment it is attached to is taken too,
    for a caller that writes it."""
    if lock:
        await db.execute(
            select(Environment.id)
            .join(
                EnvironmentAttachment,
                EnvironmentAttachment.environment_id == Environment.id,
            )
            .where(EnvironmentAttachment.session_id == session_id)
            .with_for_update()
        )
    found = (
        await db.execute(
            select(*attachment_columns()).where(AgentSession.id == session_id)
        )
    ).first()
    return attachment(found) if found is not None else None


async def environments_of(
    db: AsyncSession, session_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict]:
    """The environment each of these sessions is attached to, by session; a
    session attached to none is absent."""
    if not session_ids:
        return {}
    rows = await db.execute(
        select(AgentSession.id, environment_column()).where(
            AgentSession.id.in_(session_ids)
        )
    )
    return {sid: env for sid, env in rows.all() if env is not None}


def place(
    runtime_location: dict | None, environment: dict | None
) -> SessionPlace | None:
    """Where a session is: the machine its process runs on
    (``runtime_location``) and the ``environment`` it is attached to. None
    while it has no process anywhere; a session with no environment that
    works in a scratch area of its own on its machine is placed there
    (``runtime_location["scratch"]``). The one construction of a
    `SessionPlace`."""
    if not runtime_location:
        return None
    return SessionPlace(
        machine=runtime_location["device_id"],
        channel=runtime_location["channel"],
        resource_id=runtime_location["resource_id"],
        runtime=runtime_location.get("runtime") or {},
        lease=environment or runtime_location.get("scratch"),
    )


async def place_of(db: AsyncSession, row: AgentSession) -> SessionPlace | None:
    """Where the session ``row`` is (`place`)."""
    if not row.runtime_location:
        return None
    held = await attachment_of(db, row.id)
    return place(row.runtime_location, held.environment if held else None)


def is_attached() -> ColumnElement[bool]:
    """A session that is attached to an environment, for a query over
    ``agent_sessions``."""
    return exists().where(EnvironmentAttachment.session_id == AgentSession.id)


def attached_on(device_id: Any) -> ColumnElement[bool]:
    """A session attached to an environment on ``device_id`` (a value or a
    column), for a query over ``agent_sessions``; served by an index."""
    return exists().where(
        EnvironmentAttachment.session_id == AgentSession.id,
        Environment.id == EnvironmentAttachment.environment_id,
        Environment.device_id == device_id,
    )


def environment_device() -> ColumnElement[str]:
    """The device a session's environment is on, as a selectable column."""
    return (
        select(Environment.device_id)
        .join(
            EnvironmentAttachment,
            EnvironmentAttachment.environment_id == Environment.id,
        )
        .where(EnvironmentAttachment.session_id == AgentSession.id)
        .correlate(AgentSession)
        .scalar_subquery()
    )


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
    """What a session's environment is when an attach creates it, or moves it
    to another place: the conversation whose machine it is (its holder), its
    project, the choice it is for, and the platform host and sandbox home it
    is on."""

    topic_id: uuid.UUID
    project_id: uuid.UUID | None
    choice: dict
    host_id: uuid.UUID | None = None
    sandbox_home_id: uuid.UUID | None = None


def _moment(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _write(environment: Environment, described: dict) -> None:
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


async def attach(
    db: AsyncSession,
    session_id: uuid.UUID,
    described: dict,
    *,
    where: Where | None = None,
) -> None:
    """The session's environment is now as ``described`` (the dict
    `environment_column` reads back), its attachment under the generation it
    names. A session attached to none is attached to a new environment
    ``where`` says; ``where`` given for one already attached moves its
    environment there."""
    current = await db.scalar(
        select(EnvironmentAttachment).where(
            EnvironmentAttachment.session_id == session_id
        )
    )
    environment = (
        await db.get(Environment, current.environment_id)
        if current is not None
        else None
    )
    if environment is None:
        if where is None:
            # i18n-exempt: developer miswiring, not a refusal a reader can act on
            raise ValueError("an environment is created only where it is placed")
        environment = Environment(
            topic_id=where.topic_id, project_id=where.project_id, choice=where.choice
        )
        db.add(environment)
    if where is not None:
        environment.topic_id = where.topic_id
        environment.project_id = where.project_id
        environment.choice = where.choice
        environment.host_id = where.host_id
        environment.sandbox_home_id = where.sandbox_home_id
    _write(environment, described)
    await db.flush()
    if current is None:
        current = EnvironmentAttachment(
            id=session_id,
            session_id=session_id,
            environment_id=environment.id,
            generation=described["generation"],
        )
        db.add(current)
    current.generation = described.get("generation") or current.generation
    current.setup_digest = described.get("setup_digest")
    await db.flush()


async def _environment_of(
    db: AsyncSession, session_id: uuid.UUID
) -> Environment | None:
    return await db.scalar(
        select(Environment)
        .join(
            EnvironmentAttachment,
            EnvironmentAttachment.environment_id == Environment.id,
        )
        .where(EnvironmentAttachment.session_id == session_id)
    )


async def _let_go(db: AsyncSession, session_id: uuid.UUID, *, keep: bool) -> None:
    """End the session's attachment. The environment goes with it unless
    ``keep`` (its directories on a person's machine, which the cleanup still
    removes) or another session is still attached to it."""
    environment = await _environment_of(db, session_id)
    await db.execute(
        delete(EnvironmentAttachment).where(
            EnvironmentAttachment.session_id == session_id
        )
    )
    if environment is None or keep:
        await db.flush()
        return
    others = await db.scalar(
        select(EnvironmentAttachment.id)
        .where(EnvironmentAttachment.environment_id == environment.id)
        .limit(1)
    )
    if others is None:
        await db.delete(environment)
    await db.flush()


async def detach(
    db: AsyncSession,
    row: AgentSession,
    *,
    keep: bool = False,
    left_host: uuid.UUID | None = None,
) -> None:
    """The session leaves its environment for another: nothing minted for its
    attachment acts for it any more; ``keep`` leaves the environment for the
    cleanup to remove its directories on a person's machine; and
    ``left_host`` is the host its conversation asked to be moved off. Whatever
    its agent was owed about the environment it left is dropped on purpose:
    it describes a place the agent no longer works in."""
    await _let_go(db, row.id, keep=keep)
    if left_host is not None:
        await db.execute(
            insert(ConversationEnvironment)
            .values(conversation_id=row.conversation_id, left_host_id=left_host)
            .on_conflict_do_update(
                index_elements=[ConversationEnvironment.conversation_id],
                set_={"left_host_id": left_host},
            )
        )
    row.environment_notice = None


async def unattach(db: AsyncSession, session_id: uuid.UUID) -> None:
    """The environment the session was attached to is gone with nothing to
    keep (a whole VM given back, a machine unbound): it is attached to none,
    and the notices it is owed stand."""
    await _let_go(db, session_id, keep=False)


async def left_host_of(db: AsyncSession, conversation_id: uuid.UUID) -> str | None:
    """The host this conversation asked to be moved off and has not been
    placed off since."""
    found = await db.scalar(
        select(ConversationEnvironment.left_host_id).where(
            ConversationEnvironment.conversation_id == conversation_id
        )
    )
    return str(found) if found else None


async def placed_off(db: AsyncSession, conversation_id: uuid.UUID) -> None:
    """The conversation was placed somewhere again: the host it asked to be
    moved off no longer matters. Written only when there is one to forget."""
    await db.execute(
        update(ConversationEnvironment)
        .where(
            ConversationEnvironment.conversation_id == conversation_id,
            ConversationEnvironment.left_host_id.is_not(None),
        )
        .values(left_host_id=None)
    )


async def _release(db: AsyncSession, session_id: uuid.UUID, device_id: str | None):
    """The session's environment, if on ``device_id``, is released: checked
    by nobody since, and the claim of an install that was running on it
    ended with it."""
    environment = await _environment_of(db, session_id)
    if environment is not None and environment.device_id == device_id:
        environment.status = RELEASED
        environment.verified_at = None
        environment.claim = None
        environment.claim_until = None
        await db.flush()


async def lose(
    db: AsyncSession, row: AgentSession, device_id: str | None, *, stopped=None
) -> None:
    """The session's sandbox on ``device_id`` was destroyed: its agent is owed
    that (with what was ``stopped``, when the host could say; None keeps what
    was already owed), and the environment, if it is on that device, is
    released."""
    owed = dict(row.environment_notice or {})
    if stopped is not None:
        owed.pop(_LOST_PROCESSES, None)
        if stopped:
            owed[_LOST_PROCESSES] = list(stopped)
    owed[_LOST] = True
    row.environment_notice = owed
    await _release(db, row.id, device_id)


async def park(
    db: AsyncSession, row: AgentSession, device_id: str | None, stopped: list[str]
) -> None:
    """The session's sandbox on ``device_id`` was parked: its processes
    (``stopped``) stopped, its home kept. Its agent is owed that, and the
    environment, if on that device, is released."""
    row.environment_notice = {**(row.environment_notice or {}), _PARKED: stopped}
    await _release(db, row.id, device_id)


def take_notice(row: AgentSession) -> Notice:
    """What the session's agent is owed, handed over once: it is cleared here
    and the caller tells it."""
    owed = _notice(row.environment_notice)
    if owed.owed:
        row.environment_notice = None
    return owed


async def unchecked(db: AsyncSession, session_id: uuid.UUID) -> None:
    """The environment's executor is to be checked whole before it is handed
    out again (``verified_at`` dropped)."""
    environment = await _environment_of(db, session_id)
    if environment is not None and environment.verified_at is not None:
        environment.verified_at = None
        await db.flush()


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


async def environment_of_conversation(
    db: AsyncSession, conversation_id: uuid.UUID
) -> AgentSession | None:
    """The session whose environment is the conversation's: of its placed
    sessions, the one attached to an environment, else the one placed last.
    None while no session of it was ever placed."""
    rows = (
        await db.execute(
            select(AgentSession, is_attached())
            .where(
                AgentSession.conversation_id == conversation_id,
                AgentSession.runtime_location.is_not(None),
            )
            .order_by(AgentSession.updated_at.desc())
        )
    ).all()
    on_machine = [row for row, attached in rows if attached]
    return (on_machine or [row for row, _ in rows] or [None])[0]
