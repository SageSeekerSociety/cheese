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
from typing import Any

from sqlalchemy import (
    ColumnElement,
    String,
    cast,
    delete,
    exists,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import AgentSession, SessionPlace
from app.domain.environment import services as environment_rows
from app.domain.environment.models import (
    RELEASED,
    ConversationEnvironment,
    Environment,
    EnvironmentAttachment,
)

# What an agent is owed, as its session's ``environment_notice`` keeps it.
_LOST = "lost"
_LOST_PROCESSES = "lost_processes"
_PARKED = "parked"


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
    #: its executor is installed there. None while it is attached to none, or
    #: to one not yet placed on a machine.
    environment: dict | None
    #: What its agent is owed about it.
    notice: Notice
    #: The environment it is attached to, placed or not; None while it is
    #: attached to none.
    environment_id: uuid.UUID | None = None
    #: This attachment's generation: what a credential minted for it names,
    #: and what an install checks it is still installing for.
    generation: str | None = None

    @property
    def released(self) -> bool:
        """Its environment's sandbox was destroyed or parked since it attached:
        it names where the machine was, and nothing there answers."""
        return bool(self.environment) and self.environment.get("status") == RELEASED

    @property
    def device_id(self) -> str | None:
        return (self.environment or {}).get("device_id")


def environment_column() -> ColumnElement[Any]:
    """The environment a session is attached to, as one dict labelled
    ``environment`` (None while it is attached to none), for a query over
    ``agent_sessions``: the environment as `environment_rows.described`
    reads it, with the attachment's generation and setup digest."""
    described = environment_rows.described(
        environment_rows.key("session_id"),
        cast(EnvironmentAttachment.session_id, String),
        environment_rows.key("generation"),
        EnvironmentAttachment.generation,
        environment_rows.key("setup_digest"),
        EnvironmentAttachment.setup_digest,
    )
    return (
        select(described)
        .join(
            EnvironmentAttachment,
            EnvironmentAttachment.environment_id == Environment.id,
        )
        .where(
            EnvironmentAttachment.session_id == AgentSession.id,
            # One not yet on a machine names nothing a call could reach.
            Environment.device_id.is_not(None),
        )
        .correlate(AgentSession)
        .scalar_subquery()
        .label("environment")
    )


def _attached(column: Any, label: str) -> ColumnElement[Any]:
    return (
        select(column)
        .where(EnvironmentAttachment.session_id == AgentSession.id)
        .correlate(AgentSession)
        .scalar_subquery()
        .label(label)
    )


#: The columns `attachment` reads, for a query that selects them instead of
#: the whole row.
def attachment_columns() -> tuple[Any, ...]:
    return (
        environment_column(),
        AgentSession.environment_notice,
        _attached(EnvironmentAttachment.environment_id, "attached_to"),
        _attached(EnvironmentAttachment.generation, "attached_generation"),
    )


def attachment(row: Any) -> Attachment:
    """The attachment a row of `attachment_columns` describes. The one
    construction of an `Attachment`."""
    return Attachment(
        environment=row.environment,
        notice=_notice(row.environment_notice),
        environment_id=row.attached_to,
        generation=row.attached_generation,
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
    """A session that is attached to an environment on a machine, for a query
    over ``agent_sessions``."""
    return exists().where(
        EnvironmentAttachment.session_id == AgentSession.id,
        Environment.id == EnvironmentAttachment.environment_id,
        Environment.device_id.is_not(None),
    )


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


async def settle(
    db: AsyncSession,
    row: AgentSession,
    *,
    choice: dict,
    default: dict,
    where: environment_rows.Where,
    own: bool = False,
) -> Environment:
    """Attach the session to the environment its next call works in, and
    answer it. The one rule for every attacher:

    - Its ``choice`` is the conversation's ``default`` (compared by value):
      the environment the conversation points to, when that one is of the
      default; otherwise a new one of it. Either way the conversation points
      to it, even from an environment of another choice: every attacher with
      the default works in one environment, and a changed default moves only
      whoever attaches after it.
    - Its choice is its own (it differs from the default, or ``own``: an
      agent on its owner's computer, which never takes the conversation's):
      the environment it is attached to while that one is still of its
      choice, otherwise a new one. That one takes the conversation's
      pointer only while the conversation points to nothing usable (`_usable`):
      a conversation points to the place its work is in, which is the
      default's when anybody uses it and the one in use otherwise.

    A session moved to another environment is attached there under a new
    generation, so nothing minted for the one it left acts for it."""
    current = await db.scalar(
        select(EnvironmentAttachment)
        .where(EnvironmentAttachment.session_id == row.id)
        .with_for_update()
    )
    attached = (
        await db.get(Environment, current.environment_id, populate_existing=True)
        if current is not None
        else None
    )
    pointer = await environment_rows.pointer(db, row.conversation_id)
    pointed = (
        await db.get(Environment, pointer.environment_id)
        if pointer.environment_id is not None
        else None
    )
    if not own and choice == default:
        target = pointed
        if target is None or not environment_rows.is_of(target, default, where):
            target = environment_rows.new_environment(db, where)
            await db.flush()
        pointer.environment_id = target.id
    else:
        if attached is not None and environment_rows.is_of(attached, choice, where):
            target = attached
        else:
            target = environment_rows.new_environment(db, where)
            await db.flush()
        if not await _usable(db, pointed, default, where, row.id):
            pointer.environment_id = target.id
    if attached is not None and attached.id == target.id:
        await db.flush()
        return target
    await db.flush()
    if current is not None:
        # One left on a person's machine keeps its directories there for the
        # cleanup to remove.
        await _let_go(
            db,
            row.id,
            keep=attached is not None and environment_rows.on_a_persons(attached),
        )
        # Owed about the place it left; it works somewhere else now.
        row.environment_notice = None
    db.add(
        EnvironmentAttachment(
            id=row.id,
            session_id=row.id,
            environment_id=target.id,
            generation=str(uuid.uuid4()),
        )
    )
    await db.flush()
    return target


async def _usable(
    db: AsyncSession,
    pointed: Environment | None,
    default: dict,
    where: environment_rows.Where,
    session_id: uuid.UUID,
) -> bool:
    """Whether the environment a conversation points to is still the place
    its work is in: of the holder's current generation, and of the default
    choice or worked in by somebody besides the session settling now."""
    if pointed is None or pointed.room_resource_id != where.generation:
        return False
    if pointed.choice == default:
        return True
    return bool(
        await db.scalar(
            select(
                exists().where(
                    EnvironmentAttachment.environment_id == pointed.id,
                    EnvironmentAttachment.session_id != session_id,
                )
            )
        )
    )


async def abandon(db: AsyncSession, row: AgentSession) -> None:
    """The environment the session is attached to is on a machine nothing can
    reach any more (a person's computer unbound): the session leaves it, and
    the conversation stops pointing to it, so the next to settle gets a new
    one."""
    environment = await _environment_of(db, row.id)
    if environment is None:
        return
    await db.execute(
        update(ConversationEnvironment)
        .where(ConversationEnvironment.environment_id == environment.id)
        .values(environment_id=None)
    )
    await _let_go(db, row.id, keep=False)


async def attach(db: AsyncSession, session_id: uuid.UUID, described: dict) -> None:
    """The environment the session is attached to is now as ``described``
    (the dict `environment_column` reads back), and the session's
    attachment carries the generation and setup digest it names. A session
    attached to none is `settle`d first; this is a caller's mistake."""
    current = await db.scalar(
        select(EnvironmentAttachment).where(
            EnvironmentAttachment.session_id == session_id
        )
    )
    if current is None:
        # i18n-exempt: developer miswiring, not a refusal a reader can act on
        raise ValueError("a session is settled in an environment before it is placed")
    environment = await db.get(Environment, current.environment_id)
    assert environment is not None  # the attachment's key
    environment_rows.write(environment, described)
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
    """End the session's attachment. The environment goes with it when
    nobody is left in it and no conversation points to it, unless ``keep``
    (its directories on a person's machine, which the cleanup still
    removes). A pointed one stays, with or without a sandbox, for the next
    to attach to find."""
    environment = await _environment_of(db, session_id)
    await db.execute(
        delete(EnvironmentAttachment).where(
            EnvironmentAttachment.session_id == session_id
        )
    )
    if environment is None or keep:
        await db.flush()
        return
    await environment_rows.forget_if_unused(db, environment.id)


async def detach(
    db: AsyncSession,
    row: AgentSession,
    *,
    keep: bool = False,
    left_host: uuid.UUID | None = None,
    another: bool = False,
) -> None:
    """The session leaves its environment for another: nothing minted for its
    attachment acts for it any more; ``keep`` leaves the environment for the
    cleanup to remove its directories on a person's machine; and
    ``left_host`` is the host its conversation asked to be moved off.
    ``another``: it asked for another environment of the choice it already
    has — then the conversation stops pointing to the one it is leaving, so
    the next to attach with the default gets a new one, while whoever else
    is in the old one stays until their next attach. Whatever its agent was
    owed about the environment it left is dropped on purpose: it describes a
    place the agent no longer works in."""
    if left_host is not None or another:
        leaving = await _environment_of(db, row.id)
        pointer = await environment_rows.pointer(db, row.conversation_id)
        if left_host is not None:
            pointer.left_host_id = left_host
        if another and leaving is not None and pointer.environment_id == leaving.id:
            pointer.environment_id = None
        await db.flush()
    await _let_go(db, row.id, keep=keep)
    row.environment_notice = None


async def unattach(db: AsyncSession, session_id: uuid.UUID) -> None:
    """The environment the session was attached to is gone with nothing to
    keep (a whole VM given back, a machine unbound): it is attached to none,
    and the notices it is owed stand."""
    await _let_go(db, session_id, keep=False)


def _pointers_of(environment_id: uuid.UUID) -> ColumnElement[bool]:
    """The pointers of the conversations working in the environment: the one
    that points to it, and those of the sessions attached to it."""
    return (ConversationEnvironment.environment_id == environment_id) | (
        ConversationEnvironment.conversation_id.in_(
            select(AgentSession.conversation_id)
            .join(
                EnvironmentAttachment,
                EnvironmentAttachment.session_id == AgentSession.id,
            )
            .where(EnvironmentAttachment.environment_id == environment_id)
        )
    )


async def left_host_of(db: AsyncSession, environment_id: uuid.UUID) -> str | None:
    """The host a conversation working in this environment asked to be
    moved off, and has not been placed off since."""
    found = await db.scalar(
        select(ConversationEnvironment.left_host_id)
        .where(
            _pointers_of(environment_id),
            ConversationEnvironment.left_host_id.is_not(None),
        )
        .limit(1)
    )
    return str(found) if found else None


async def placed_off(db: AsyncSession, environment_id: uuid.UUID) -> None:
    """The environment was placed somewhere again: the host its
    conversations asked to be moved off no longer matters. Written only when
    there is one to forget."""
    await db.execute(
        update(ConversationEnvironment)
        .where(
            _pointers_of(environment_id),
            ConversationEnvironment.left_host_id.is_not(None),
        )
        .values(left_host_id=None)
    )


async def _release(db: AsyncSession, session_id: uuid.UUID, device_id: str | None):
    environment = await _environment_of(db, session_id)
    if environment is not None:
        await environment_rows.release(db, environment.id, device_id)


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
