"""Authorize a subscription on the computer running the command, without enrollment."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import ForbiddenError
from app.core.sentences import say
from app.domain.agent import credential_login
from app.domain.team import permissions as team_seats
from app.domain.team.permissions import Permission as TeamPermission

router = APIRouter(prefix="/connector", tags=["connector"])


class StartLoginOut(BaseModel):
    ticket: str
    server: str
    command: str


class RegisterLoginIn(BaseModel):
    ticket: str
    transport_token: str = Field(default="", max_length=128)
    computer_name: str = Field(min_length=1, max_length=255)


class RegisterLoginOut(BaseModel):
    login_id: str
    token: str
    device_id: str
    credential_host: bool


@router.post("/teams/{team_id}/claude-login", dependencies=[ROUTE_DECIDES])
async def start_login(
    team_id: int, resolver: ActorResolverDep, db: DbSession
) -> StartLoginOut:
    actor = await resolver.resolve()
    if actor.user_id is None or not await team_seats.may(
        db, team_id, actor.user_id, TeamPermission.manage_machines
    ):
        raise ForbiddenError(say("deviceTeamAdminOnly"))
    return StartLoginOut(**await credential_login.start(db, team_id, actor.user_id))


@router.post("/model-login/register", dependencies=[ROUTE_DECIDES])
async def register_login(body: RegisterLoginIn, db: DbSession) -> RegisterLoginOut:
    return RegisterLoginOut.model_validate(
        await credential_login.register(
            db, body.ticket, body.transport_token, body.computer_name
        )
    )


class RefreshLoginIn(BaseModel):
    token: str = Field(max_length=128)


class RefreshLoginOut(BaseModel):
    refreshed: bool


@router.post("/model-login/refresh", dependencies=[ROUTE_DECIDES])
async def refresh_login(body: RefreshLoginIn, db: DbSession) -> RefreshLoginOut:
    return RefreshLoginOut(refreshed=await credential_login.refresh(db, body.token))
