"""Which execution environment a session is attached to, and what its agent is
owed about it: the one place that reads or changes either.

Everything that asks which machine and home a session works in, who is
attached to an environment, or what notice an agent is owed goes through the
functions here, and nothing else touches where that is stored. A caller gets
an `Attachment` (what the session's attachment and the environment it is on
say now) and changes it only by naming what happened: it attached, it
detached, the environment was released under it, its sandbox was lost or
parked, a thaw was asked for.

It sits in ``agent_session`` because every reader can import it from here:
the session-picking readers in ``repository`` and ``room_task`` and
``memory`` are below ``machine`` in the layer table (``backend/.importlinter``).
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, String, cast, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import AgentSession, SessionPlace

#: The ``status`` of an environment whose sandbox was destroyed (idle, or
#: given up with its host) or parked with its processes stopped: it still
#: names the machine and home it was on, and nothing there answers for the
#: session's work until an attach replaces it with a ready one. Whether a
#: machine holds the work is this; whether the agent has been told it lost
#: its sandbox is its `Notice`, which the first tool call that carries a
#: notice takes, whoever brought the machine back.
RELEASED = "released"

# Where each fact is kept today. A caller names the fact, never these; only
# tests that seed or read the stored row spell them.
LOST_KEY = "sandbox_lost"
LOST_PROCESSES_KEY = "sandbox_lost_processes"
PARKED_KEY = "sandbox_parked"
LEFT_HOST_KEY = "sandbox_left_host"
THAW_KEY = "sandbox_thaw"
_LEFT = "retained_leases"
_GENERATION = "generation"

#: The device an attached environment is on, spelled as
#: ``ix_agent_sessions_lease_device`` indexes it. The key is a literal, not
#: bound, so the index serves a prepared statement's generic plan too.
_ENVIRONMENT_DEVICE = literal_column(
    "(agent_sessions.work_lease ->> 'device_id')", String
)

#: The columns `attachment` reads, for a query that selects them instead of
#: the whole row.
ATTACHMENT_COLUMNS = (AgentSession.work_lease, AgentSession.execution_request)


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


@dataclass(frozen=True, slots=True)
class Attachment:
    """A session's attachment and the environment it is on, as they stand."""

    #: The environment it is attached to: which machine and home, and how far
    #: its executor is installed there. None while it is attached to none.
    environment: dict | None
    #: This attachment's generation: what a credential minted for it names,
    #: and what an install checks it is still installing for.
    generation: str | None
    #: What its agent is owed about it.
    notice: Notice
    #: The host it asked to be moved off and has not been placed off since.
    left_host: str | None
    #: The resource of a frozen sandbox a tool call asked to wake, until the
    #: install thaws it.
    thaw: str | None
    #: Environments it left on people's machines, whose directories the
    #: cleanup still removes.
    left: tuple[dict, ...] = field(default=())

    @property
    def released(self) -> bool:
        """Its environment's sandbox was destroyed or parked since it attached:
        it names where the machine was, and nothing there answers."""
        return bool(self.environment) and self.environment.get("status") == RELEASED

    @property
    def device_id(self) -> str | None:
        return (self.environment or {}).get("device_id")

    def environments(self) -> list[dict]:
        """Every environment it holds or left: those it left first."""
        return environments_held(self.environment, list(self.left))


def attachment(row: Any) -> Attachment:
    """The attachment a session row (or a row of `ATTACHMENT_COLUMNS`)
    stores. The one construction of an `Attachment`."""
    request = row.execution_request or {}
    lost = bool(request.get(LOST_KEY))
    parked = request.get(PARKED_KEY) is not None
    stopped = request.get(LOST_PROCESSES_KEY if lost else PARKED_KEY) or []
    left_host = request.get(LEFT_HOST_KEY)
    thaw = request.get(THAW_KEY)
    return Attachment(
        environment=row.work_lease,
        generation=request.get(_GENERATION),
        notice=Notice(
            lost=lost,
            stopped=tuple(str(one) for one in stopped if one),
            parked=parked,
        ),
        left_host=str(left_host) if left_host else None,
        thaw=thaw or None,
        left=tuple(request.get(_LEFT) or ()),
    )


async def attachment_of(db: AsyncSession, session_id: uuid.UUID) -> Attachment | None:
    """This session's attachment as committed now, read without taking its
    row; None when there is no such session."""
    found = (
        await db.execute(
            select(*ATTACHMENT_COLUMNS).where(AgentSession.id == session_id)
        )
    ).first()
    return attachment(found) if found is not None else None


def place(
    runtime_location: dict | None, environment: dict | None
) -> SessionPlace | None:
    """Where a session is: the machine its process runs on
    (``runtime_location``) and the ``environment`` it is attached to. None
    while it has no process anywhere. The one construction of a
    `SessionPlace`."""
    if not runtime_location:
        return None
    return SessionPlace(
        machine=runtime_location["device_id"],
        channel=runtime_location["channel"],
        resource_id=runtime_location["resource_id"],
        runtime=runtime_location.get("runtime") or {},
        lease=environment,
    )


def place_of(row: AgentSession) -> SessionPlace | None:
    """Where the session ``row`` is (`place`)."""
    return place(row.runtime_location, row.work_lease)


def environment_column() -> ColumnElement[Any]:
    """The environment a session is attached to, as a column labelled
    ``environment``, for a query that reads it without the whole row."""
    return AgentSession.work_lease.label("environment")


def attached_values(environment: dict | None) -> dict[str, Any]:
    """The column values that attach a session to ``environment``, for an
    upsert of its row that writes them with others."""
    return {"work_lease": environment}


def is_attached() -> ColumnElement[bool]:
    """A session that is attached to an environment, for a query over
    ``agent_sessions``."""
    return AgentSession.work_lease.is_not(None)


def attached_on(device_id: Any) -> ColumnElement[bool]:
    """A session attached to an environment on ``device_id`` (a value or a
    column), for a query over ``agent_sessions``; served by an index."""
    return _ENVIRONMENT_DEVICE == device_id


def environment_device() -> ColumnElement[str]:
    """The device a session's environment is on, as a selectable column."""
    return AgentSession.work_lease["device_id"].as_string()


def mentions(value: str) -> ColumnElement[bool]:
    """A session whose attachment, or an environment it left, names ``value``
    anywhere (a device id, a resource id): the broad match a cleanup needs
    before it judges each row through `attachment`."""
    return AgentSession.work_lease.cast(String).contains(
        value
    ) | AgentSession.execution_request.cast(String).contains(value)


#: The columns `environments_held` reads: the environment a session is
#: attached to and those it left, without decoding the rest of its row.
ENVIRONMENTS_HELD_COLUMNS = (
    AgentSession.work_lease,
    AgentSession.execution_request[_LEFT],
)


def environments_held(current: dict | None, left: list[dict] | None) -> list[dict]:
    """Every environment a session holds or left on people's machines, from a
    row of `ENVIRONMENTS_HELD_COLUMNS`: those it left first."""
    return [*(left or []), *([current] if current else [])]


def held_or_left_on(device_id: str) -> ColumnElement[bool]:
    """A session attached to an environment on ``device_id``, or that left
    one there. The second arm has no index."""
    return or_(
        attached_on(device_id),
        cast(AgentSession.execution_request, JSONB)[_LEFT].contains(
            [{"device_id": device_id}]
        ),
    )


def begin(row: AgentSession) -> str:
    """This session's attachment generation, starting one when it has none:
    a turn about to run, or an install about to attach it, names it."""
    if not row.execution_request:
        row.execution_request = {_GENERATION: str(uuid.uuid4())}
    return row.execution_request[_GENERATION]


def attach(row: AgentSession, environment: dict | None) -> None:
    """Attach the session to ``environment`` as it now stands (or record how
    far attaching it has got); None is attached to none."""
    row.work_lease = environment


def detach(
    row: AgentSession, *, keep: Iterable[dict] = (), left_host: str | None = None
) -> None:
    """The session leaves its environment for another: a new generation, so
    nothing minted for the old one acts for it; ``keep`` names environments
    on people's machines whose directories the cleanup still removes; and
    ``left_host`` the host it asked to be moved off. Whatever its agent was
    owed about the environment it left is dropped on purpose: it describes a
    place the agent no longer works in."""
    request = row.execution_request or {}
    changed: dict = {
        _GENERATION: str(uuid.uuid4()),
        _LEFT: [*request.get(_LEFT, []), *keep],
    }
    if left_host is not None:
        changed[LEFT_HOST_KEY] = left_host
    row.execution_request = changed
    row.work_lease = None


def unattach(row: AgentSession) -> None:
    """The environment the session was attached to is gone with nothing to
    keep (a whole VM given back): it is attached to none, and its
    generation, notices and the rest stand."""
    row.work_lease = None


def released(environment: dict | None) -> dict | None:
    """``environment`` as it stands once its sandbox is gone (`RELEASED`)."""
    if environment is None:
        return None
    return {
        **{
            key: value
            for key, value in environment.items()
            if key not in ("verified_at", "claim", "claim_until")
        },
        "status": RELEASED,
    }


def lose(row: AgentSession, device_id: str | None, *, stopped=None) -> None:
    """The session's sandbox on ``device_id`` was destroyed: its agent is owed
    that (with what was ``stopped``, when the host could say; None keeps what
    was already owed), and the environment, if it is on that device, is
    released: checked by nobody since, and the claim of an install that was
    running on it ended with it."""
    if stopped is None:
        row.execution_request = {**(row.execution_request or {}), LOST_KEY: True}
    else:
        request = {
            key: value
            for key, value in (row.execution_request or {}).items()
            if key != LOST_PROCESSES_KEY
        }
        request[LOST_KEY] = True
        if stopped:
            request[LOST_PROCESSES_KEY] = list(stopped)
        row.execution_request = request
    if (row.work_lease or {}).get("device_id") == device_id:
        row.work_lease = released(row.work_lease)


def park(row: AgentSession, device_id: str | None, stopped: list[str]) -> None:
    """The session's sandbox on ``device_id`` was parked: its processes
    (``stopped``) stopped, its home kept. Its agent is owed that, a thaw asked
    for it is moot, and the environment, if on that device, is released."""
    request = {
        key: value
        for key, value in (row.execution_request or {}).items()
        if key != THAW_KEY
    }
    request[PARKED_KEY] = stopped
    row.execution_request = request
    if (row.work_lease or {}).get("device_id") == device_id:
        row.work_lease = released(row.work_lease)


def take_notice(row: AgentSession) -> Notice:
    """What the session's agent is owed, handed over once: it is cleared here
    and the caller tells it."""
    owed = attachment(row).notice
    if owed.owed:
        row.execution_request = {
            key: value
            for key, value in (row.execution_request or {}).items()
            if key not in (LOST_KEY, LOST_PROCESSES_KEY, PARKED_KEY)
        }
    return owed


def want_thaw(row: AgentSession, resource_id: str) -> None:
    """A tool call wants the frozen sandbox ``resource_id`` woken; the install
    thaws it before its executor is asked anything."""
    row.execution_request = {**(row.execution_request or {}), THAW_KEY: resource_id}


def thawed(row: AgentSession, resource_id: str | None = None) -> None:
    """The thaw asked for (only ``resource_id``'s, when named) is done or moot."""
    request = row.execution_request or {}
    if resource_id is not None:
        if request.get(THAW_KEY) != resource_id:
            return
        left = dict(request)
        left.pop(THAW_KEY, None)
        row.execution_request = left
        return
    row.execution_request = {
        key: value for key, value in request.items() if key != THAW_KEY
    }


def placed_off(row: AgentSession) -> None:
    """The session was placed somewhere again: the host it asked to be moved
    off no longer matters. Written only when there is one to forget."""
    request = row.execution_request or {}
    if LEFT_HOST_KEY not in request:
        return
    row.execution_request = {
        key: value for key, value in request.items() if key != LEFT_HOST_KEY
    }


def unchecked(row: AgentSession) -> None:
    """The environment's executor is to be checked whole before it is handed
    out again (``verified_at`` dropped)."""
    if row.work_lease and "verified_at" in row.work_lease:
        row.work_lease = {
            key: value for key, value in row.work_lease.items() if key != "verified_at"
        }


async def distrust(
    db: AsyncSession, session_id: uuid.UUID, generation: str | None
) -> None:
    """A call on the session's environment failed on the machine's side: the
    next request for it checks it whole. Only this session's row is taken, and
    only what says the environment was checked is read and written — the
    execution route calls this, served by the device connection owner."""
    lease = await db.scalar(
        select(AgentSession.work_lease)
        .where(AgentSession.id == session_id)
        .with_for_update()
    )
    if lease and "verified_at" in lease and lease.get("generation") == generation:
        await db.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(work_lease={k: v for k, v in lease.items() if k != "verified_at"})
        )


async def environment_of_conversation(
    db: AsyncSession, conversation_id: uuid.UUID
) -> AgentSession | None:
    """The session whose environment is the conversation's: of its placed
    sessions, the one attached to an environment on a machine, else the one
    placed last. None while no session of it was ever placed."""
    rows = list(
        await db.scalars(
            select(AgentSession)
            .where(
                AgentSession.conversation_id == conversation_id,
                AgentSession.runtime_location.is_not(None),
            )
            .order_by(AgentSession.updated_at.desc())
        )
    )
    on_machine = [row for row in rows if (row.work_lease or {}).get("kind") == "device"]
    return (on_machine or rows or [None])[0]
