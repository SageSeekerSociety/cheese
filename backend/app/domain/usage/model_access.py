"""Which models a team's plan lets it pick, and which plan a model needs
(#2397).

A plan allows model tiers; a person picking a model never sees a tier. A model
the team's plan does not allow is shown with the cheapest plan that does, by
its name. Plans carry no price yet, so "cheapest" is the order an administrator
gives them (``Plan.rank``), with a plan anyone can be put on before an
administrator's own.
Only plans the team could be put on count: a personal team is never told it
needs a plan only shared teams may have.

An overseas model (Claude, GPT, Gemini) also needs the team approved by a
platform administrator (``OverseasModelApproval``), whatever its plan allows.
A team without that approval is not shown such a model at all (``offered``):
it is not something the team could get by changing plan.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_facts import recall, remember
from app.domain.team.services import team_service
from app.domain.usage.ledger import Terms, team_terms
from app.domain.usage.models import OverseasModelApproval, Plan


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
    #: Whether an administrator has approved the team for overseas models.
    overseas_approved: bool = False

    def allows_tier(self, tier: str) -> bool:
        return self.allowed_tiers is None or tier in self.allowed_tiers

    def shows(self, choice: dict) -> bool:
        """Is ``choice`` (a ``model_choices`` entry) shown to the team at all."""
        return self.overseas_approved or not choice.get("overseas")

    def allows(self, choice: dict) -> bool:
        """May the team use ``choice``."""
        return self.shows(choice) and self.allows_tier(choice.get("tier", ""))

    def minimum_plan(self, tier: str) -> str | None:
        """The name of the cheapest plan that allows ``tier``; None when the
        team's own plan does, or when no plan it could be on does."""
        if self.allows_tier(tier):
            return None
        return next((p.name for p in self.plans if _allows(p, tier)), None)

    def mark(self, choice: dict) -> dict:
        """``choice`` (a ``model_choices`` entry), with whether the plan allows
        it and, when not, the plan that would."""
        return {
            **choice,
            "allowed": self.allows(choice),
            "requires_plan": self.minimum_plan(choice.get("tier", "")),
        }

    def offered(self, choices: list[dict]) -> list[dict]:
        """``choices`` as the team's picker lists them: marked, and without the
        ones it is not shown."""
        return [self.mark(choice) for choice in choices if self.shows(choice)]


async def model_access(session: AsyncSession, team_id: int) -> ModelAccess:
    if isinstance(session, AsyncSession):
        if session.autoflush:
            await session.flush()
        found, cached = recall(session, ("model_access", team_id))
        if found:
            return cached

    team = await team_service(session).get_team(team_id)
    personal = team is not None and team.personal_owner_user_id is not None
    all_plans = list((await session.execute(select(Plan))).scalars())
    plans = sorted(
        (p for p in all_plans if _fits(p, personal=personal)), key=_cheapness
    )
    by_key = {p.key: p for p in all_plans}
    plan_key = team.plan_key if team is not None else "free"
    if plan_key in by_key:
        allowed_tiers = Terms.of(by_key[plan_key]).model_tiers
    else:
        allowed_tiers = (await team_terms(session, team_id)).model_tiers
    out = ModelAccess(
        allowed_tiers=allowed_tiers,
        plans=tuple(plans),
        overseas_approved=await overseas_approved(session, team_id),
    )
    if isinstance(session, AsyncSession):
        remember(
            session,
            ("model_access", team_id),
            out,
            tables=frozenset({"team", "plans", "overseas_model_approvals"}),
        )
    return out


async def overseas_approved(session: AsyncSession, team_id: int) -> bool:
    """Has an administrator approved the team for overseas models."""
    return await session.get(OverseasModelApproval, team_id) is not None
