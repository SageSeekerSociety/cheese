"""Read-only running sessions for device viewers and the local connector."""

from uuid import UUID

from fastapi import APIRouter

from app.api.auth import ActorResolverDep, PresentedCredential
from app.api.request_deps import DbSession
from app.api.routes.connector.schemas import RunningDeviceSessionsOut
from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.domain.agent.device_sessions import running_on
from app.domain.device import owner_reads
from app.domain.device.wiring import sql_device_service
from app.domain.team.services import team_service

router = APIRouter(prefix="/connector", tags=["connector"])


# Connector clients consume raw JSON, so document it without the platform envelope.
@router.get(
    "/devices/{device_id}/running-sessions",
    response_model=None,
    responses={200: {"model": RunningDeviceSessionsOut}},
)
async def device_sessions(device_id: str, resolver: ActorResolverDep, db: DbSession):
    actor = await resolver.resolve()
    if not actor.authenticated or actor.user_id is None:
        raise UnauthorizedError()
    device = await sql_device_service(db).get_device(device_id)
    if device is None:
        raise NotFoundError()
    if not await team_service(db).is_team_member(device.team_id, actor.user_id):
        raise ForbiddenError()
    placed = await running_on(db, device_id)
    readable = set()
    for project_id in {row["project_id"] for _, row in placed}:
        readable |= await resolver.readable_topic_ids(
            actor,
            project_id=UUID(project_id),
            topics=[topic for topic, row in placed if row["project_id"] == project_id],
        )
    return {"sessions": [row for topic, row in placed if topic.id in readable]}


@router.get(
    "/device/running-sessions",
    response_model=None,
    responses={200: {"model": RunningDeviceSessionsOut}},
)
async def connector_sessions(db: DbSession, token: PresentedCredential):
    device = await owner_reads.device_for_token(db, token)
    if device is None:
        raise UnauthorizedError()
    return {"sessions": [row for _, row in await running_on(db, device.device_id)]}
