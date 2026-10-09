"""What a finished turn is recorded as using, and what it is charged.

Each turn leaves usage rows in the ledger (`usage/ledger.py`), one per model. A
turn routed through the gateway is read back from the gateway's spend; a turn
on the platform's subscription is landed from the metering proxy's log; a
member's own Claude Code (#2991) runs on its owner's login, so its own report is
the only record of it, kept and charged nothing.
"""

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.agent.gateway_usage import OWN_ROUTE
from app.domain.agent.service import AgentUsage
from app.domain.usage.credits import spend_to_credits
from app.domain.usage.ledger import Ledger, payer_for_project

if TYPE_CHECKING:
    from app.domain.agent.turn.state.live import HookWorkState


async def record_turn_usage(
    session: AsyncSession,
    state: "HookWorkState",
    usages: list[AgentUsage],
    *,
    gateway_charged: bool,
) -> None:
    """Write the turn's usage rows. ``gateway_charged``: the gateway's spend
    was read back and charged already (`charge_turn_spend`)."""
    payer = await payer_for_project(session, state.project_id)
    if not usages:
        await Ledger(session).record(
            payer,
            credits=0.0,
            topic_id=state.topic_id,
            model=state.model or settings.agent_model,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            metered=False,
            route=state.route,
            turn_id=state.work_id,
        )
        return
    if gateway_charged:
        return
    # A member's own Claude Code ran on its owner's login: what it did is
    # recorded, and none of it is charged to the project.
    own = state.route == OWN_ROUTE
    for u in usages:
        await Ledger(session).record(
            payer,
            credits=0.0 if own else spend_to_credits(u.cost_usd),
            topic_id=state.topic_id,
            model=u.model or state.model or ("" if own else settings.agent_model),
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cost_usd=u.cost_usd,
            route=state.route,
            turn_id=state.work_id,
        )
