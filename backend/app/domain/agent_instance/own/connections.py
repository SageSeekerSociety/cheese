"""Team model connections, on a device or encrypted on the platform."""

import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance import account_connections
from app.domain.agent_instance.models import AccountConnection, PersonalModelResource
from app.domain.device.login_kind import (
    FIRST_LOGIN,
    ConnectionKind,
)
from app.domain.device.models import (
    DeviceClaudeLoginRow,
    DeviceRow,
    ModelCredentialHost,
)


@dataclass(frozen=True)
class Login:
    """A team or personal credential, stored on a computer or on the platform.
    Its storage computer is independent of its team and member ownership."""

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
    #: The machine holding it; None for an encrypted platform-held connection.
    device_id: str | None = None
    device_name: str | None = None
    #: The connection's chosen name.
    name: str | None = None
    allowed_models: tuple[str, ...] = ()
    owner_user_id: int | None = None
    storage_team_id: int | None = None

    def storage_name(self, team_id: int, viewer: int | None) -> str | None:
        if self.storage_team_id == team_id or (
            self.owner_user_id is not None and self.owner_user_id == viewer
        ):
            return self.device_name
        return self.device_id

    @property
    def on_account(self) -> bool:
        return self.device_id is None


def _login(
    device_id: str,
    name: str,
    row: DeviceClaudeLoginRow,
    storage_team_id: int | None = None,
) -> Login:
    return Login(
        id=row.id,
        device_id=device_id,
        device_name=name,
        kind=ConnectionKind.subscription,
        plan=row.subscription_type,
        model=None,
        name=row.name,
        allowed_models=tuple(row.allowed_models),
        owner_user_id=row.owner_user_id,
        storage_team_id=storage_team_id,
    )


def _held(row: AccountConnection) -> Login:
    return Login(
        id=str(row.id),
        kind=ConnectionKind(row.kind),
        plan=None,
        model=row.model,
        service_host=account_connections.host_of(row),
        name=row.name,
        allowed_models=tuple(row.allowed_models),
        owner_user_id=row.owner_user_id,
    )


def _usable():
    """A login is usable while logged in and not revoked."""
    return (
        DeviceClaudeLoginRow.logged_in.is_(True),
        DeviceClaudeLoginRow.auth_method == "claude.ai",
        DeviceClaudeLoginRow.revoked_at.is_(None),
    )


async def logins(db: AsyncSession, team_id: int) -> list[Login]:
    """The team's device logins, most recently confirmed first, followed by
    connections stored encrypted on the platform, oldest first."""
    return (await logins_by_teams(db, {team_id}))[team_id]


async def logins_by_teams(
    db: AsyncSession, team_ids: set[int]
) -> dict[int, list[Login]]:
    """Credentials native to each team and members' personal references."""
    result: dict[int, list[Login]] = {owner: [] for owner in team_ids}
    if not team_ids:
        return result
    rows = await db.execute(
        select(
            DeviceClaudeLoginRow,
            DeviceRow.name,
            DeviceRow.team_id,
            ModelCredentialHost.name,
        )
        .outerjoin(DeviceRow, DeviceRow.device_id == DeviceClaudeLoginRow.device_id)
        .outerjoin(
            ModelCredentialHost, ModelCredentialHost.id == DeviceClaudeLoginRow.host_id
        )
        .where(
            or_(
                DeviceClaudeLoginRow.team_id.in_(team_ids),
                DeviceClaudeLoginRow.id.in_(
                    select(PersonalModelResource.login_id).where(
                        PersonalModelResource.team_id.in_(team_ids)
                    )
                ),
            ),
            *_usable(),
        )
        # One machine's logins are confirmed in one answer: the first
        # login first, then the same order on every read.
        .order_by(
            DeviceClaudeLoginRow.checked_at.desc(),
            DeviceClaudeLoginRow.local_id != FIRST_LOGIN,
            DeviceClaudeLoginRow.local_id,
        )
    )
    refs = list(
        await db.scalars(
            select(PersonalModelResource).where(
                PersonalModelResource.team_id.in_(team_ids)
            )
        )
    )
    for row, name, storage_team, host_name in rows:
        login = _login(
            row.transport_id, name or host_name or row.transport_id, row, storage_team
        )
        destinations = {r.team_id for r in refs if r.login_id == row.id}
        if row.team_id in team_ids:
            destinations.add(row.team_id)
        for team in destinations:
            result[team].append(login)
    held = await db.scalars(
        select(AccountConnection)
        .where(
            or_(
                AccountConnection.team_id.in_(team_ids),
                AccountConnection.id.in_(
                    select(PersonalModelResource.account_connection_id).where(
                        PersonalModelResource.team_id.in_(team_ids)
                    )
                ),
            )
        )
        .order_by(AccountConnection.created_at)
    )
    for row in held:
        destinations = {r.team_id for r in refs if r.account_connection_id == row.id}
        if row.team_id in team_ids:
            destinations.add(row.team_id)
        for team in destinations:
            result[team].append(_held(row))
    return result


def _connection_key(
    login_id: str | None, account_connection_id: uuid.UUID | None
) -> str | None:
    """The `Login.id` of the connection an own agent's row names."""
    if login_id is not None:
        return login_id
    return str(account_connection_id) if account_connection_id else None


def label(login: Login) -> str:
    """Label a teammate with its connection's name, falling back to the
    subscription plan, service model, or API-key kind."""
    if login.name:
        return login.name
    if login.kind is ConnectionKind.subscription:
        return login.plan.capitalize() if login.plan else "订阅"
    if login.kind is ConnectionKind.model_service:
        return login.model or login.service_host or "自定义模型"
    return "API"
