"""Directory grants on team-managed devices.

Current team administrators list, grant and revoke directories on a device.
The grant creator and revoker remain audit facts. The account access log
continues to show the caller's own access history.

Writes push the device's whole live grant set after committing. An offline
device receives the current set when it reconnects.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import (
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.agent.device_hub import device_hub
from app.domain.device.wiring import sql_device_service
from app.domain.local_fs.enforcement import PushOutcome, push_grants
from app.domain.local_fs.paths import Platform
from app.domain.local_fs.records import (
    AccessRecord,
    DirectoryGrant,
    GrantMode,
    GrantScope,
)
from app.domain.local_fs.service import GrantRefused, LocalDirectoryService
from app.domain.local_fs.wiring import sql_local_directory_service

router = APIRouter(prefix="/connector", tags=["local-directories"])


class GrantDirectoryRequest(BaseModel):
    """Authorize one directory on one of the caller's machines.

    ``platform`` is required rather than guessed. Only the machine can tell
    macOS from Linux, and the difference is real — one is case-insensitive and
    the other is not — so a wrong guess here would become a grant that silently
    fails to cover its own files.
    """

    path: str = Field(min_length=1)
    platform: Platform
    mode: GrantMode = GrantMode.READ
    scope: GrantScope = GrantScope.PROJECT
    project_id: uuid.UUID | None = None


async def _require_user(resolver: ActorResolverDep) -> int:
    actor = await resolver.resolve()
    if not actor.authenticated or actor.user_id is None:
        raise UnauthorizedError(say("localDirSignIn"))
    return actor.user_id


async def _require_managed_device(
    db: AsyncSession, user_id: int, device_id: str
) -> None:
    """Refuse unless the caller administers this device's team.

    A device that exists but belongs to somebody else is reported as missing, so
    the endpoint cannot be used to enumerate other people's machines.
    """
    device = await sql_device_service(db).get_hosted_device(device_id)
    if device is None or not await sql_device_service(db).may_manage(device, user_id):
        raise NotFoundError(say("deviceNotYours"))


def _grant_view(grant: DirectoryGrant) -> dict[str, Any]:
    return {
        "id": str(grant.id),
        "device_id": grant.device_id,
        "path": grant.path,
        "platform": grant.platform.value,
        "mode": grant.mode.value,
        "scope": grant.scope.value,
        "project_id": str(grant.project_id) if grant.project_id else None,
        "created_at": grant.created_at.isoformat(),
        "revoked_at": grant.revoked_at.isoformat() if grant.revoked_at else None,
    }


def _delivery_view(outcome: PushOutcome) -> dict[str, Any]:
    """What to tell the owner about the trip to their machine.

    Kept OUT of the grant view rather than added to it: the grant's own fields are
    a deliberately closed set (see the sharing guard test), and 「下发成功了没有」
    is a fact about one attempt, not a property of the grant.
    """
    return {
        "delivered": outcome.delivered,
        "reason": outcome.reason,
        "detail": outcome.detail,
    }


def _access_view(record: AccessRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "device_id": record.device_id,
        "path": record.path,
        "mode": record.mode.value,
        "decision": record.decision.value,
        "reason": record.reason,
        "detail": record.detail,
        "actor_handle": record.actor_handle,
        "project_id": str(record.project_id) if record.project_id else None,
        "conversation_id": str(record.conversation_id)
        if record.conversation_id
        else None,
        "created_at": record.created_at.isoformat(),
    }


@router.get("/my/devices/{device_id}/directories")
async def list_directories(
    device_id: str,
    db: DbSession,
    resolver: ActorResolverDep,
    include_revoked: bool = Query(default=False),
) -> dict[str, Any]:
    """The directories authorized on this machine."""
    user_id = await _require_user(resolver)
    await _require_managed_device(db, user_id, device_id)
    grants = await sql_local_directory_service(db).list_grants(
        device_id, include_revoked=include_revoked
    )
    return {"directories": [_grant_view(g) for g in grants]}


@router.post("/my/devices/{device_id}/directories", dependencies=[ROUTE_DECIDES])
async def grant_directory(
    device_id: str,
    body: GrantDirectoryRequest,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict[str, Any]:
    """Authorize a directory, or refuse with a reason the screen can show."""
    user_id = await _require_user(resolver)
    await _require_managed_device(db, user_id, device_id)
    service: LocalDirectoryService = sql_local_directory_service(db)
    try:
        grant = await service.grant_directory(
            device_id=device_id,
            owner_user_id=user_id,
            path=body.path,
            platform=body.platform,
            mode=body.mode,
            scope=body.scope,
            project_id=body.project_id,
        )
    except GrantRefused as refused:
        # Deliberately a 422 carrying the refusal's own words: 「只能授权具体目录」
        # is actionable and 「拒绝访问」 is not.
        raise ValidationError(refused.detail) from refused
    await db.commit()
    # After the commit, not before: the grant is now the authoritative record, and
    # a machine that is offline has not made it any less real. A failure here is
    # reported, never raised — see the module docstring.
    outcome = await push_grants(service, device_hub, device_id, end_read=db.commit)
    return {**_grant_view(grant), "delivery": _delivery_view(outcome)}


@router.delete(
    "/my/devices/{device_id}/directories/{grant_id}", dependencies=[ROUTE_DECIDES]
)
async def revoke_directory(
    device_id: str,
    grant_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict[str, Any]:
    """Revoke a grant. Effective on the next question asked, with no restart."""
    user_id = await _require_user(resolver)
    await _require_managed_device(db, user_id, device_id)
    service = sql_local_directory_service(db)
    grant = await service.revoke(grant_id, actor_user_id=user_id, device_id=device_id)
    if grant is None:
        raise NotFoundError(say("localDirGrantNotFound"))
    await db.commit()
    # Pushed immediately, and that is the point of a revocation: the device holds
    # its own copy and would otherwise keep honoring a grant its owner has just
    # taken away until something else happened to re-send the set.
    outcome = await push_grants(service, device_hub, device_id, end_read=db.commit)
    return {"revoked": True, "id": str(grant.id), "delivery": _delivery_view(outcome)}


@router.get("/my/access-log")
async def my_access_log(
    db: DbSession,
    resolver: ActorResolverDep,
    device_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    """What was read or written across the caller's machines — and what was
    refused, which is the half a log of successes cannot show."""
    user_id = await _require_user(resolver)
    if device_id is not None:
        # The query is scoped by owner either way, so a stranger's device id
        # would just come back empty — which reads as 「没有记录」 for a machine
        # that is not yours. Saying so plainly is better than an empty list that
        # means two different things.
        await _require_managed_device(db, user_id, device_id)
    records = await sql_local_directory_service(db).list_access(
        user_id, device_id=device_id, limit=limit
    )
    return {"records": [_access_view(r) for r in records]}
