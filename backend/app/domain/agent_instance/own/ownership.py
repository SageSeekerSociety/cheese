"""Whose own agent a row is, and which connection it runs on.

The answer every caller of this package starts from: an ``Owned`` row read from
the instance id, or from the seat or session handle a caller already holds, plus
the connection that row names and what its owner is called.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.models import AccountConnection, AgentInstance, OwnAgent
from app.domain.agent_instance.own.connections import (
    Login,
    logins_by_teams,
)
from app.domain.device.models import DeviceClaudeLoginRow
from app.domain.identity.handles import agent_instance_handle
from app.domain.project.models import Project
from app.domain.user.models import User
from app.domain.user.services import faces_by_handle


@dataclass(frozen=True)
class Owned:
    instance_id: uuid.UUID
    owner_user_id: int
    harness: str
    #: The machine holding the model connection it runs on.
    device_id: str | None = None
    #: Or the connection the team stores encrypted on the platform.
    account_connection_id: uuid.UUID | None = None
    #: The login on that machine it runs on (`DeviceClaudeLoginRow.id`).
    login_id: str | None = None

    @property
    def on_account(self) -> bool:
        """It runs on a connection the team stores encrypted on the platform, so on the
        deployment's session host rather than its owner's machine."""
        return self.account_connection_id is not None


_OWNED = (
    AgentInstance.id,
    OwnAgent.owner_user_id,
    OwnAgent.harness,
    func.coalesce(DeviceClaudeLoginRow.host_id, DeviceClaudeLoginRow.device_id),
    OwnAgent.account_connection_id,
    OwnAgent.login_id,
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
        .outerjoin(DeviceClaudeLoginRow, DeviceClaudeLoginRow.id == OwnAgent.login_id)
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
            .outerjoin(
                DeviceClaudeLoginRow, DeviceClaudeLoginRow.id == OwnAgent.login_id
            )
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
    login = await db.get(DeviceClaudeLoginRow, row.login_id) if row.login_id else None
    return Owned(
        row.instance_id,
        row.owner_user_id,
        row.harness,
        login.transport_id if login is not None else None,
        row.account_connection_id,
        row.login_id,
    )


async def owned_in_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    instances: Sequence[AgentInstance] | None = None,
) -> list[tuple[AgentInstance, int]]:
    """The project's personal agents and their owners, including inactive ones."""
    owner_id = func.coalesce(
        AccountConnection.owner_user_id, DeviceClaudeLoginRow.owner_user_id
    )
    if instances is not None:
        by_id = {instance.id: instance for instance in instances}
        if not by_id:
            return []
        rows = await db.execute(
            select(OwnAgent.instance_id, owner_id)
            .outerjoin(
                AccountConnection,
                AccountConnection.id == OwnAgent.account_connection_id,
            )
            .outerjoin(
                DeviceClaudeLoginRow, DeviceClaudeLoginRow.id == OwnAgent.login_id
            )
            .where(OwnAgent.instance_id.in_(list(by_id)), owner_id.is_not(None))
        )
        return [
            (by_id[instance_id], owner)
            for instance_id, owner in rows
            if instance_id in by_id
        ]
    rows = await db.execute(
        select(AgentInstance, owner_id)
        .join(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
        .outerjoin(
            AccountConnection, AccountConnection.id == OwnAgent.account_connection_id
        )
        .outerjoin(DeviceClaudeLoginRow, DeviceClaudeLoginRow.id == OwnAgent.login_id)
        .where(AgentInstance.project_id == project_id, owner_id.is_not(None))
    )
    return [(instance, owner) for instance, owner in rows]


async def owned_rows_in_project(
    db: AsyncSession, project_id: uuid.UUID
) -> list[tuple[AgentInstance, Owned]]:
    """The project's instances and their ownership, including inactive ones."""
    rows = await db.execute(
        select(AgentInstance, *_OWNED[1:])
        .join(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
        .outerjoin(DeviceClaudeLoginRow, DeviceClaudeLoginRow.id == OwnAgent.login_id)
        .where(AgentInstance.project_id == project_id)
        .order_by(AgentInstance.created_at)
    )
    return [(instance, Owned(instance.id, *values)) for instance, *values in rows]


async def connection_of(db: AsyncSession, owned: Owned) -> Login | None:
    """The model connection a member's own agent runs on, while it is one:
    None once its owner removed it, logged it out, or the machine is gone."""
    return (await connections_of(db, [owned]))[owned.instance_id]


async def connections_of(
    db: AsyncSession, owners: list[Owned]
) -> dict[uuid.UUID, Login | None]:
    teams: dict[uuid.UUID, int] = {
        instance_id: team_id
        for instance_id, team_id in (
            await db.execute(
                select(AgentInstance.id, Project.team_id)
                .join(Project, Project.id == AgentInstance.project_id)
                .where(AgentInstance.id.in_([o.instance_id for o in owners]))
            )
        ).all()
    }
    by_team = await logins_by_teams(db, set(teams.values()))
    connections: dict[tuple[int | None, bool, str | None], Login] = {
        (team_id, login.on_account, login.id): login
        for team_id, logins in by_team.items()
        for login in logins
    }
    result = {
        owned.instance_id: connections.get(
            (
                teams.get(owned.instance_id),
                owned.on_account,
                str(owned.account_connection_id)
                if owned.on_account
                else owned.login_id,
            )
        )
        for owned in owners
    }
    return {
        owned.instance_id: (
            login
            if login is not None
            and (
                login.owner_user_id is None
                or login.owner_user_id == owned.owner_user_id
            )
            else None
        )
        for owned in owners
        for login in [result[owned.instance_id]]
    }


async def owner_of(db: AsyncSession, instance_id: uuid.UUID) -> tuple[str, str] | None:
    """The handle and nickname of whoever this agent belongs to, or None when
    it is the project's own."""

    owned = await owned_instance(db, instance_id)
    if owned is None:
        return None
    credential_owner = await db.scalar(
        select(AccountConnection.owner_user_id).where(
            AccountConnection.id == owned.account_connection_id
        )
        if owned.on_account
        else select(DeviceClaudeLoginRow.owner_user_id).where(
            DeviceClaudeLoginRow.id == owned.login_id
        )
    )
    if credential_owner is None:
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
