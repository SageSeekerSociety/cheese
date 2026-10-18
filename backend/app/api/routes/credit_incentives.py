"""People claim beta credits; team administrators submit purchase enquiries."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

from app.api.admin_deps import PlatformAdminDep
from app.api.auth import ActorResolverDep
from app.api.request_deps import AuthUser, DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.auth.checker import require_permission
from app.auth.core import Action, AuthUserInfo, Resource
from app.core.errors import BadRequestError, ForbiddenError
from app.core.sentences import say
from app.domain.authz import policy
from app.domain.identity.services import IdentityService
from app.domain.usage.incentives import CreditIncentives
from app.domain.usage.schemas import (
    BetaCreditsOut,
    CreditRequestOut,
    CreditRequestsOut,
    CreditRewardOut,
)
from app.domain.user.services import usernames_by_ids

router = APIRouter(tags=["credits"])
TeamId = Annotated[int, Path(ge=1, alias="teamId")]
TeamReader = Annotated[
    AuthUserInfo, require_permission(Action.READ, Resource.TEAM_MEMBERSHIP, "teamId")
]
TeamManager = Annotated[
    AuthUserInfo, require_permission(Action.UPDATE, Resource.TEAM, "teamId")
]


class CreditRequestIn(BaseModel):
    kind: Literal["team_plan", "top_up"]
    credits: int | None = Field(default=None, ge=100, le=1000000)


class CreditRequestHandle(BaseModel):
    approve: bool
    payment_confirmed: bool = False
    plan_key: str | None = Field(default=None, max_length=32)


async def require_human(
    db: DbSession, user: AuthUser, resolver: ActorResolverDep
) -> None:
    actor = await resolver.resolve()
    refused = await policy.refuse_management_action(
        actor, carries_agent_binding=IdentityService(db).is_agent
    )
    if refused is not None:
        raise ForbiddenError(refused)


@router.get("/users/me/credits/beta", **typed_response(BetaCreditsOut))
async def beta_credits(db: DbSession, user: AuthUser) -> dict:
    return ok(await CreditIncentives(db).beta(user.user_id))


@router.post(
    "/users/me/credits/beta",
    dependencies=[ROUTE_DECIDES, Depends(require_human)],
    **typed_response(CreditRewardOut),
)
async def claim_beta_credits(db: DbSession, user: AuthUser) -> dict:
    result = await CreditIncentives(db).claim_beta(user.user_id)
    await db.commit()
    return ok(result)


@router.get("/teams/{teamId}/credits/requests", **typed_response(CreditRequestsOut))
async def credit_requests(team_id: TeamId, db: DbSession, user: TeamReader) -> dict:
    return ok({"items": await CreditIncentives(db).requests(team_id)})


@router.post(
    "/teams/{teamId}/credits/requests",
    dependencies=[ROUTE_DECIDES, Depends(require_human)],
    **typed_response(CreditRequestOut),
)
async def request_credits(
    team_id: TeamId, body: CreditRequestIn, db: DbSession, user: TeamManager
) -> dict:
    if body.kind == "top_up" and body.credits is None:
        raise BadRequestError(say("creditAmountRequired"))
    handle = (await usernames_by_ids(db, [user.user_id]))[user.user_id]
    result = await CreditIncentives(db).request(
        team_id, kind=body.kind, credits=body.credits, actor=handle
    )
    await db.commit()
    return ok(result)


@router.get("/admin/credits/requests", **typed_response(CreditRequestsOut))
async def pending_credit_requests(db: DbSession, handle: PlatformAdminDep) -> dict:
    return ok({"items": await CreditIncentives(db).requests()})


@router.post(
    "/admin/credits/requests/{request_id}",
    dependencies=[ROUTE_DECIDES],
    **typed_response(CreditRequestOut),
)
async def handle_credit_request(
    request_id: uuid.UUID,
    body: CreditRequestHandle,
    db: DbSession,
    handle: PlatformAdminDep,
) -> dict:
    if body.approve and not body.payment_confirmed:
        raise BadRequestError(say("creditPaymentRequired"))
    result = await CreditIncentives(db).handle_request(
        request_id, actor=handle, approve=body.approve, plan_key=body.plan_key
    )
    await db.commit()
    return ok(result)
