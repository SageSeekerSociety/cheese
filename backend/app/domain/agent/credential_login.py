"""Issue and redeem model login authorizations independently of device enrollment."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select

from app.core import scoped_jwt
from app.core.config import settings
from app.core.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.core.sentences import say
from app.domain.agent.device_hub import device_hub
from app.domain.agent.harness.claude_code import owner_login
from app.domain.device.models import (
    DeviceClaudeLoginRow,
    DeviceRow,
    ModelCredentialHost,
)
from app.domain.team import permissions as team_seats
from app.domain.team.permissions import Permission as TeamPermission
from app.domain.team.services import team_service
from app.domain.user.tokens import digest_token

DOMAIN = b"cheese:model-credential-login:v1"


async def start(db, team_id: int, user_id: int):
    team = await team_service(db).get_team(team_id)
    owner = team.personal_owner_user_id if team else None
    ticket = scoped_jwt.sign(
        {
            "aud": "model-credential-login",
            "exp": datetime.now(UTC) + timedelta(minutes=15),
            "team": team_id,
            "owner": owner,
            "user": user_id,
            "id": uuid.uuid4().hex,
        },
        domain=DOMAIN,
    )
    server = settings.frontend_url.rstrip("/") + "/api/connector"
    return dict(
        ticket=ticket,
        server=server,
        command=f"cheesehost claude login --server {server} --ticket {ticket}",
    )


async def register(db, ticket: str, transport_token: str, computer_name: str):
    try:
        claims = scoped_jwt.verify(
            ticket,
            domain=DOMAIN,
            audience="model-credential-login",
            require=["exp", "team", "user", "id"],
        )
    except jwt.PyJWTError:
        raise UnauthorizedError(say("modelLoginAuthorizationInvalid")) from None
    if not await team_seats.may(
        db, claims["team"], claims["user"], TeamPermission.manage_machines
    ):
        raise ForbiddenError(say("deviceTeamAdminOnly"))
    if await db.get(DeviceClaudeLoginRow, claims["id"]) is not None:
        raise ConflictError(say("modelLoginAuthorizationUsed"))
    digest = digest_token(transport_token) if transport_token else None
    device = (
        await db.scalar(select(DeviceRow).where(DeviceRow.token_digest == digest))
        if digest
        else None
    )
    host = (
        await db.scalar(
            select(ModelCredentialHost).where(
                ModelCredentialHost.token_digest == digest
            )
        )
        if digest
        else None
    )
    # The machine keeps the token it came with; a computer that came with none
    # (or one nobody knows) is given its own, here, once.
    token = transport_token if (device or host) else secrets.token_urlsafe(48)
    if host is None:
        host = ModelCredentialHost(
            id=device.device_id if device else uuid.uuid4().hex,
            token_digest=digest_token(token),
            name=device.name if device else computer_name,
        )
        db.add(host)
        await db.flush()
    row = DeviceClaudeLoginRow(
        id=claims["id"],
        local_id=claims["id"],
        device_id=device.device_id if device else None,
        host_id=host.id,
        team_id=claims["team"],
        owner_user_id=claims.get("owner"),
        installed=False,
        logged_in=False,
        checked_at=datetime.now(UTC),
        allowed_models=["opus", "sonnet", "haiku"],
    )
    db.add(row)
    await db.commit()
    return dict(
        login_id=row.local_id,
        token=token,
        device_id=row.transport_id,
        credential_host=device is None,
    )


async def refresh(db, token: str) -> bool:
    digest = digest_token(token)
    device = await db.scalar(
        select(DeviceRow.device_id).where(DeviceRow.token_digest == digest)
    )
    host = await db.scalar(
        select(ModelCredentialHost.id).where(ModelCredentialHost.token_digest == digest)
    )
    target = device or host
    if not target:
        raise UnauthorizedError(say("modelLoginConnectionUnknown"))
    await db.commit()
    if device_hub.is_online(target):
        reports = await owner_login.ask(device_hub, target)
        await owner_login.remember(db, target, reports)
        await db.commit()
        return True
    return False
