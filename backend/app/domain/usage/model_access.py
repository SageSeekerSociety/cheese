"""Which models a team's plan lets it pick, and which plan a model needs
(#2397).

A plan allows model tiers; a person picking a model never sees a tier. A model
the team's plan does not allow is shown with the cheapest plan that does, by
its name. Plans carry no price yet, so "cheapest" is the order an administrator
gives them (``Plan.rank``), with a plan anyone can be put on before an
administrator's own.
Only plans the team could be put on count: a personal team is never told it
needs a plan only shared teams may have.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.team.services import team_service
from app.domain.usage.ledger import team_terms
from app.domain.usage.models import Plan


def _fits(plan: Plan, *, personal: bool) -> bool:
    return plan.audience == "both" or plan.audience == (
        "personal" if personal else "team"
    )


def _allows(plan: Plan, tier: str) -> bool:
    return plan.model_tiers is None or tier in plan.model_tiers


def _cheapness(plan: Plan) -> tuple:
    return (plan.admin_only, plan.rank, plan.key)


@dataclass(frozen=True)
class ModelAccess:
    """The model tiers a team's plan allows, and every plan it could be on."""

    #: None is every tier.
    allowed_tiers: frozenset[str] | None
    #: The plans the team could be put on, cheapest first.
    plans: tuple[Plan, ...]

    def allows(self, tier: str) -> bool:
        return self.allowed_tiers is None or tier in self.allowed_tiers

    def minimum_plan(self, tier: str) -> str | None:
        """The name of the cheapest plan that allows ``tier``; None when the
        team's own plan does, or when no plan it could be on does."""
        if self.allows(tier):
            return None
        return next((p.name for p in self.plans if _allows(p, tier)), None)

    def mark(self, choice: dict) -> dict:
        """``choice`` (a ``model_choices`` entry), with whether the plan allows
        it and, when not, the plan that would."""
        tier = choice.get("tier", "")
        return {
            **choice,
            "allowed": self.allows(tier),
            "requires_plan": self.minimum_plan(tier),
        }


async def model_access(session: AsyncSession, team_id: int) -> ModelAccess:

    team = await team_service(session).get_team(team_id)
    personal = team is not None and team.personal_owner_user_id is not None
    rows = await session.execute(select(Plan))
    plans = sorted(
        (p for p in rows.scalars() if _fits(p, personal=personal)), key=_cheapness
    )
    terms = await team_terms(session, team_id)
    return ModelAccess(allowed_tiers=terms.model_tiers, plans=tuple(plans))
