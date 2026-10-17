"""Whose own agent a row is, and which connection it runs on.

The answer every caller of this package starts from: an ``Owned`` row read from
the instance id, or from the seat or session handle a caller already holds, plus
the connection that row names and what its owner is called.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance import account_connections
from app.domain.agent_instance.models import AgentInstance, OwnAgent, OwnCallers
from app.domain.agent_instance.own.connections import Login, _held, logins
from app.domain.identity.handles import agent_instance_handle
from app.domain.user.models import User
from app.domain.user.services import faces_by_handle


@dataclass(frozen=True)
class Owned:
    instance_id: uuid.UUID
    owner_user_id: int
    harness: str
    callers: OwnCallers = OwnCallers.owner
    #: The machine holding the model connection it runs on.
    device_id: str | None = None
    #: Or the connection its owner's account holds.
    account_connection_id: uuid.UUID | None = None

    @property
    def on_account(self) -> bool:
        """It runs on a connection its owner's account holds, so on the
        deployment's session host rather than its owner's machine."""
        return self.account_connection_id is not None


_OWNED = (
    AgentInstance.id,
    OwnAgent.owner_user_id,
    OwnAgent.harness,
    OwnAgent.callers,
    OwnAgent.device_id,
    OwnAgent.account_connection_id,
)


async def owned_by_seat(
    db: AsyncSession, project_id: uuid.UUID, seat_handle: str | None
) -> Owned | None:
    """The owner of the agent that sits on rosters as ``seat_handle`` in this
    project, or None when that agent is not anyone's own (the project's 芝士,
    another teammate, a person)."""
    if not seat_handle:
        return None
    rows = await db.execute(
        select(*_OWNED)
        .join(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
        .where(AgentInstance.project_id == project_id)
    )
    for row in rows:
        if agent_instance_handle(row[0]) == seat_handle:
            return Owned(*row)
    return None


async def owned_by_session(
    db: AsyncSession, project_id: uuid.UUID, session_handle: str | None
) -> Owned | None:
    """The owner of the agent a session in this project runs for. A session
    names its agent by the instance's own handle (``AgentInstance.handle``),
    not by the seat it is listed under."""
    if not session_handle:
        return None
    row = (
        await db.execute(
            select(*_OWNED)
            .join(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
            .where(
                AgentInstance.project_id == project_id,
                AgentInstance.handle == session_handle,
            )
        )
    ).first()
    return Owned(*row) if row is not None else None


async def owned_instance(db: AsyncSession, instance_id: uuid.UUID) -> Owned | None:
    row = await db.get(OwnAgent, instance_id)
    if row is None:
        return None
    return Owned(
        row.instance_id,
        row.owner_user_id,
        row.harness,
        row.callers,
        row.device_id,
        row.account_connection_id,
    )


async def owned_in_project(
    db: AsyncSession, project_id: uuid.UUID
) -> list[tuple[AgentInstance, int, OwnCallers]]:
    """This project's members' own agents, each with its owner's user id and who
    may call it — those their owners removed included (``is_active`` false).
    The callers column rides along because the row holding it is this
    package's: a reader below that wants it calls this, not ``OwnAgent``."""
    rows = await db.execute(
        select(AgentInstance, OwnAgent.owner_user_id, OwnAgent.callers)
        .join(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
        .where(AgentInstance.project_id == project_id)
        .order_by(AgentInstance.created_at)
    )
    return [(instance, owner, callers) for instance, owner, callers in rows]


async def connection_of(db: AsyncSession, owned: Owned) -> Login | None:
    """The model connection a member's own agent runs on, while it is one:
    None once its owner removed it, logged it out, or the machine is gone."""
    if owned.account_connection_id is not None:
        row = await account_connections.owned(
            db, owned.owner_user_id, owned.account_connection_id
        )
        return _held(row) if row is not None else None
    if owned.device_id is None:
        return None
    for login in await logins(db, owned.owner_user_id):
        if login.device_id == owned.device_id:
            return login
    return None


async def owner_of(db: AsyncSession, instance_id: uuid.UUID) -> tuple[str, str] | None:
    """The handle and nickname of whoever this agent belongs to, or None when
    it is the project's own."""

    owned = await owned_instance(db, instance_id)
    if owned is None:
        return None
    user = await db.get(User, owned.owner_user_id)
    if user is None:
        return None
    return user.username, await _nickname(db, user.username)


async def _nickname(db: AsyncSession, handle: str) -> str:
    """What a person is called: their nickname, else their handle."""

    name, _avatar = (await faces_by_handle(db, [handle])).get(handle, (None, None))
    return name or handle


async def _nickname_of(db: AsyncSession, user_id: int) -> str:
    user = await db.get(User, user_id)
    return await _nickname(db, user.username) if user is not None else ""
