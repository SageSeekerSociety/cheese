"""Read-only running sessions for device viewers and the local connector."""

from typing import Annotated

from fastapi import APIRouter, Header

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.routes.connector.devices import DeviceServiceDep
from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.domain.agent.device_sessions import running_on
from app.domain.device import owner_reads
from app.domain.team.services import team_service

router = APIRouter(prefix="/connector", tags=["connector"])


@router.get("/devices/{device_id}/running-sessions")
async def device_sessions(
    device_id: str, resolver: ActorResolverDep, service: DeviceServiceDep, db: DbSession
):
    actor = await resolver.resolve()
    if not actor.authenticated or actor.user_id is None:
        raise UnauthorizedError()
    device = await service.get_device(device_id)
    if device is None:
        raise NotFoundError()
    teams = team_service(db)
    visible = False
    for team_id in device.team_ids:
        if await teams.is_team_member(team_id, actor.user_id):
            visible = True
            break
    if not visible:
        raise ForbiddenError()
    return {"sessions": await running_on(db, device_id)}


@router.get("/device/running-sessions")
async def connector_sessions(
    db: DbSession, token: Annotated[str | None, Header(alias="X-Cheese-Session")] = None
):
    device = await owner_reads.device_for_token(db, token or "")
    if device is None:
        raise UnauthorizedError()
    return {"sessions": await running_on(db, device.device_id)}
