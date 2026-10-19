"""A person's model connections, wherever they are held.

What one of a member's own agents runs on is one of these: the login their own
Claude Code has for the platform on one of their machines, or one their account
holds (an API key or a model service). The two are kept differently and answer
the same shape (``Login``), so the rest of this package asks one question of a
connection without knowing where it lives.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance import account_connections
from app.domain.agent_instance.models import AccountConnection
from app.domain.device.login_kind import (
    FIRST_LOGIN,
    ConnectionKind,
    connection_kind,
)
from app.domain.device.models import DeviceClaudeLoginRow, DeviceRow, HostedDeviceRow
from app.domain.device.supply import Supply


@dataclass(frozen=True)
class Login:
    """One of a person's model connections, and how it reaches a model: one
    login their own Claude Code has for the platform on one of their machines
    (a machine may hold several), or one their account holds (an API key or a
    model service). Each is named by its own row."""

    #: What the connection is named by: the login's record id, or the account
    #: connection's.
    id: str
    kind: ConnectionKind
    #: A subscription's plan (`max`, `pro`, …); None for the other kinds.
    plan: str | None
    #: The model a model service is called with; None for the other kinds.
    model: str | None
    #: A model service's host; None for the other kinds.
    service_host: str | None = None
    #: The machine holding it; None when its owner's account does.
    device_id: str | None = None
    device_name: str | None = None
    #: What its owner named the connection.
    name: str | None = None

    @property
    def on_account(self) -> bool:
        return self.device_id is None


def _login(device_id: str, name: str, row: DeviceClaudeLoginRow) -> Login:
    kind = connection_kind(row.auth_method, row.subscription_type)
    service = kind is ConnectionKind.model_service
    return Login(
        id=row.id,
        device_id=device_id,
        device_name=name,
        kind=kind,
        plan=row.subscription_type if kind is ConnectionKind.subscription else None,
        model=row.model if service else None,
        service_host=row.service_host if service else None,
        name=row.name,
    )


def _held(row: AccountConnection) -> Login:
    return Login(
        id=str(row.id),
        kind=ConnectionKind(row.kind),
        plan=None,
        model=row.model,
        service_host=account_connections.host_of(row),
        name=row.name,
    )


def _usable():
    """The conditions under which a login on a machine is a connection a session
    may run on: logged in, on a machine its owner enrolled, and not removed by
    its owner — whatever a backend that predates removals wrote back into
    ``logged_in``, it never touches ``revoked_at``."""
    return (
        DeviceRow.supply == Supply.self_hosted,
        DeviceClaudeLoginRow.logged_in.is_(True),
        DeviceClaudeLoginRow.revoked_at.is_(None),
    )


async def logins(db: AsyncSession, owner_user_id: int) -> list[Login]:
    """This person's model connections: their machines' logins, the most
    recently confirmed first, then the ones their account holds, oldest first."""
    return (await logins_by_owners(db, {owner_user_id}))[owner_user_id]


async def logins_by_owners(
    db: AsyncSession, owner_user_ids: set[int]
) -> dict[int, list[Login]]:
    """The same ordered connections for each owner, read in two queries."""
    result: dict[int, list[Login]] = {owner: [] for owner in owner_user_ids}
    if not owner_user_ids:
        return result
    rows = await db.execute(
        select(
            DeviceRow.owner_user_id,
            DeviceRow.device_id,
            DeviceRow.name,
            DeviceClaudeLoginRow,
        )
        .join(HostedDeviceRow, HostedDeviceRow.device_id == DeviceRow.device_id)
        .join(
            DeviceClaudeLoginRow,
            DeviceClaudeLoginRow.device_id == DeviceRow.device_id,
        )
        .where(DeviceRow.owner_user_id.in_(owner_user_ids), *_usable())
        # One machine's logins are confirmed in one answer: the first
        # login first, then the same order on every read.
        .order_by(
            DeviceClaudeLoginRow.checked_at.desc(),
            DeviceClaudeLoginRow.local_id != FIRST_LOGIN,
            DeviceClaudeLoginRow.local_id,
        )
    )
    for owner, device_id, name, row in rows:
        result[owner].append(_login(device_id, name, row))
    held = await db.scalars(
        select(AccountConnection)
        .where(AccountConnection.owner_user_id.in_(owner_user_ids))
        .order_by(AccountConnection.created_at)
    )
    for row in held:
        result[row.owner_user_id].append(_held(row))
    return result


def _connection_key(
    login_id: str | None, account_connection_id: uuid.UUID | None
) -> str | None:
    """The `Login.id` of the connection an own agent's row names."""
    if login_id is not None:
        return login_id
    return str(account_connection_id) if account_connection_id else None


def label(login: Login) -> str:
    """What tells one of a person's own agents from another in its name: the
    name its owner gave the connection, else a subscription's plan, a model
    service's model, or that it is an API key."""
    if login.name:
        return login.name
    if login.kind is ConnectionKind.subscription:
        return login.plan.capitalize() if login.plan else "订阅"
    if login.kind is ConnectionKind.model_service:
        return login.model or login.service_host or "自定义模型"
    return "API"
