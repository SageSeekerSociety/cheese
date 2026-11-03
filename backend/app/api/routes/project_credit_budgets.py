"""Project spending caps and the member caps that apply to task execution."""

import uuid
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import Can, InProject
from app.auth.project_roles import standing
from app.core.errors import ForbiddenError, UnprocessableEntityError
from app.core.sentences import say
from app.domain.agent_instance.adapters import project_team
from app.domain.membership.roster import roster
from app.domain.project_permissions import Permission, Seat
from app.domain.usage.budgets import CreditBudgets
from app.domain.usage.schemas import ProjectCreditBudgetOut

router = APIRouter(prefix="/projects", tags=["credits"])

CreditLimit = Annotated[float, Field(strict=True)]


class BudgetUpdate(BaseModel):
    credits_limit: CreditLimit | None = None
    member_limits: dict[str, CreditLimit | None] = Field(default_factory=dict)


async def _readable(project_id: uuid.UUID, resolver: ActorResolverDep):
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    return actor


async def _named(db: DbSession, project_id: uuid.UUID, report: dict) -> dict:
    for member in await roster(db, project_id, team=project_team):
        if member.agent:
            continue
        row = report["members"].setdefault(
            member.handle, {"credits_limit": None, "credits_used": 0.0}
        )
        row["name"] = member.name
    return report


@router.get("/{project_id}/credit-budget", **typed_response(ProjectCreditBudgetOut))
async def project_credit_budget(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    await _readable(project_id, resolver)
    return ok(await _named(db, project_id, await CreditBudgets(db).report(project_id)))


@router.put("/{project_id}/credit-budget", **typed_response(ProjectCreditBudgetOut))
async def set_project_credit_budget(
    at: Annotated[InProject, Can(Permission.edit_settings)],
    body: BudgetUpdate,
    db: DbSession,
) -> dict:
    project_id = at.project.id
    held = await standing(db, project_id=project_id, handle=at.actor.handle)
    if held is None or held.seat not in (Seat.owner, Seat.admin):
        raise ForbiddenError(say("creditBudgetAdminRequired"))
    people = await roster(db, project_id, team=project_team)
    humans = {member.handle for member in people if not member.agent}
    # Removing a departed person's cap leaves their accounting history intact.
    if any(
        limit is not None and handle not in humans
        for handle, limit in body.member_limits.items()
    ):
        raise UnprocessableEntityError(say("creditBudgetMemberRequired"))
    result = await CreditBudgets(db).save(
        project_id,
        credits_limit=body.credits_limit,
        set_project_limit="credits_limit" in body.model_fields_set,
        member_limits=body.member_limits,
    )
    result = await _named(db, project_id, result)
    await db.commit()
    return ok(result)
