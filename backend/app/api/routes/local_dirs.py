"""本机目录授权 over HTTP — the「我的电脑」screen's grants, revocations and audit.

Four endpoints, all under the owner's own login and all scoped to one of their
own machines:

* ``GET    /connector/my/devices/{device_id}/directories`` — the grants on that
  machine, with the revoked ones available on request so the screen can show
  history rather than a list that mysteriously shortened.
* ``POST   /connector/my/devices/{device_id}/directories`` — authorize one
  directory. A path that cannot be authorized comes back as a plain validation
  error carrying the Chinese reason, which is criterion five: the refusal has to
  be something a person reads, not a silent no.
* ``DELETE /connector/my/devices/{device_id}/directories/{grant_id}`` — revoke.
* ``GET    /connector/my/access-log`` — every decision across the owner's
  machines, allowed and denied alike.

Two things this module deliberately does NOT have. There is no endpoint that
lets a directory be attached to anything shareable — a grant is not a resource
and has no id another user may be handed (本机目录不参与共享). And no endpoint
reaches a grant without checking that the caller owns the device it is on: the
only id ever accepted from outside is one of the caller's own machines.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.core.db import get_db
from app.core.errors import (
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.domain.device.service import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
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

DbSession = Annotated[AsyncSession, Depends(get_db)]


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
    actor = await resolver.resolve(fallback_handle=None)
    if not actor.authenticated or actor.user_id is None:
        raise UnauthorizedError("授权本机目录需要登录")
    return actor.user_id


async def _require_owned_device(db: AsyncSession, user_id: int, device_id: str) -> None:
    """Refuse unless the caller owns this machine.

    A device that exists but belongs to somebody else is reported as missing, so
    the endpoint cannot be used to enumerate other people's machines.
    """
    device = await DeviceService(SqlDeviceRepository(db)).get_hosted_device(device_id)
    if device is None or device.owner_user_id != user_id:
        raise NotFoundError("设备不存在或不属于你")


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
        "topic_id": str(record.topic_id) if record.topic_id else None,
        "task_id": str(record.task_id) if record.task_id else None,
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
    await _require_owned_device(db, user_id, device_id)
    grants = await sql_local_directory_service(db).list_grants(
        user_id, device_id=device_id, include_revoked=include_revoked
    )
    return {"directories": [_grant_view(g) for g in grants]}


@router.post("/my/devices/{device_id}/directories")
async def grant_directory(
    device_id: str,
    body: GrantDirectoryRequest,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict[str, Any]:
    """Authorize a directory, or refuse with a reason the screen can show."""
    user_id = await _require_user(resolver)
    await _require_owned_device(db, user_id, device_id)
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
    return _grant_view(grant)


@router.delete("/my/devices/{device_id}/directories/{grant_id}")
async def revoke_directory(
    device_id: str,
    grant_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict[str, Any]:
    """Revoke a grant. Effective on the next question asked, with no restart."""
    user_id = await _require_user(resolver)
    await _require_owned_device(db, user_id, device_id)
    service = sql_local_directory_service(db)
    grant = await service.revoke(grant_id, owner_user_id=user_id)
    if grant is None or grant.device_id != device_id:
        raise NotFoundError("这条授权不存在")
    await db.commit()
    return {"revoked": True, "id": str(grant.id)}


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
        await _require_owned_device(db, user_id, device_id)
    records = await sql_local_directory_service(db).list_access(
        user_id, device_id=device_id, limit=limit
    )
    return {"records": [_access_view(r) for r in records]}
