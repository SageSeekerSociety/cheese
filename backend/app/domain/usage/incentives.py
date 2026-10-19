"""Beta rewards and the purchase enquiries handled in the credit console."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.core.sentences import say
from app.domain.team.services import team_service
from app.domain.usage.ledger import Ledger
from app.domain.usage.models import (
    CreditAdminAudit,
    CreditRequest,
    CreditReward,
    GrantSource,
)
from app.domain.usage.plans import PlanService

BETA_CREDITS = 500
FEEDBACK_CREDITS = 100


def request_out(row: CreditRequest) -> dict:
    return {
        "id": str(row.id),
        "team_id": row.team_id,
        "kind": row.kind,
        "credits": row.credits,
        "status": row.status,
        "requester_handle": row.requester_handle,
        "created_at": row.created_at.isoformat(),
    }


class CreditIncentives:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def reward(self, key: str) -> dict | None:
        row = await self._session.get(CreditReward, key)
        return {"credits": row.credits, "reason": row.reason} if row else None

    async def issue(
        self, *, key: str, user_id: int, credits: int, reason: str, actor: str
    ) -> dict:
        team = await team_service(self._session).ensure_personal_team(user_id)
        issued = await self._session.scalar(
            insert(CreditReward)
            .values(
                key=key,
                team_id=team.id,
                credits=credits,
                reason=reason,
                actor_handle=actor,
            )
            .on_conflict_do_nothing(index_elements=["key"])
            .returning(CreditReward.key)
        )
        if issued is not None:
            await Ledger(self._session).grant(team.id, credits, reason=reason)
        return {"credits": credits, "issued": issued is not None}

    async def beta(self, user_id: int) -> dict:
        return {
            "credits": BETA_CREDITS,
            "claimed": await self.reward(f"beta:{user_id}") is not None,
            "feedback_credits": FEEDBACK_CREDITS,
        }

    async def rewards(self, user_id: int) -> dict:
        team = next(
            (
                team
                for team in await team_service(self._session).get_teams_of_user(user_id)
                if team.personal_owner_user_id == user_id
            ),
            None,
        )
        if team is None:
            return {"feedback_count": 0, "feedback_credits": 0, "items": []}
        rows = list(
            await self._session.scalars(
                select(CreditReward)
                .where(CreditReward.team_id == team.id)
                .order_by(CreditReward.created_at.desc(), CreditReward.key)
            )
        )
        feedback = [row for row in rows if row.key.startswith("feedback:")]
        return {
            "feedback_count": len(feedback),
            "feedback_credits": sum(row.credits for row in feedback),
            "items": [
                {
                    "kind": "feedback" if row.key.startswith("feedback:") else "beta",
                    "credits": row.credits,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows[:10]
            ],
        }

    async def claim_beta(self, user_id: int) -> dict:
        return await self.issue(
            key=f"beta:{user_id}",
            user_id=user_id,
            credits=BETA_CREDITS,
            reason="Beta participation",
            actor=str(user_id),
        )

    async def requests(self, team_id: int | None = None) -> list[dict]:
        stmt = select(CreditRequest)
        if team_id is not None:
            stmt = stmt.where(CreditRequest.team_id == team_id)
        else:
            stmt = stmt.where(CreditRequest.status == "pending")
        rows = await self._session.scalars(
            stmt.order_by(CreditRequest.created_at.desc()).limit(100)
        )
        result = []
        teams = team_service(self._session)
        for row in rows:
            team = await teams.get_team(row.team_id)
            result.append(
                {
                    **request_out(row),
                    "team_name": team.name if team else str(row.team_id),
                }
            )
        return result

    async def request(
        self, team_id: int, *, kind: str, credits: int | None, actor: str
    ) -> dict:
        team = await team_service(self._session).get_team(team_id)
        if team is None:
            raise NotFoundError(say("teamNotFoundById", team_id=team_id))
        if kind == "team_plan" and team.personal_owner_user_id is not None:
            raise BadRequestError(say("creditSharedTeamRequired"))
        pending = await self._session.scalar(
            insert(CreditRequest)
            .values(
                team_id=team_id,
                kind=kind,
                credits=credits if kind == "top_up" else None,
                requester_handle=actor,
                status="pending",
            )
            .on_conflict_do_nothing(
                index_elements=["team_id", "kind"],
                index_where=CreditRequest.status == "pending",
            )
            .returning(CreditRequest)
        )
        if pending is None:
            raise BadRequestError(say("creditRequestPending"))
        return request_out(pending)

    async def handle_request(
        self,
        request_id: uuid.UUID,
        *,
        actor: str,
        approve: bool,
        plan_key: str | None = None,
    ) -> dict:
        row = await self._session.scalar(
            select(CreditRequest)
            .where(CreditRequest.id == request_id)
            .with_for_update()
        )
        if row is None:
            raise NotFoundError(say("creditRequestMissing"))
        if row.status != "pending":
            return request_out(row)
        if approve:
            if row.kind == "top_up":
                assert row.credits is not None
                await Ledger(self._session).grant(
                    row.team_id,
                    row.credits,
                    source=GrantSource.PURCHASE,
                    reason=f"Top-up request {row.id}",
                )
            else:
                if not plan_key:
                    raise BadRequestError(say("creditRequestPlanRequired"))
                await PlanService(self._session).set_team_plan(
                    handle=actor, team_id=row.team_id, key=plan_key
                )
        row.status = "completed" if approve else "declined"
        row.handled_by = actor
        self._session.add(
            CreditAdminAudit(
                actor_handle=actor,
                action="team.purchase_request",
                target=str(row.team_id),
                before={"request_id": str(row.id), "status": "pending"},
                after={
                    "status": row.status,
                    "kind": row.kind,
                    "credits": row.credits,
                    "plan_key": plan_key,
                },
            )
        )
        await self._session.flush()
        return request_out(row)
