"""A web search made on a gateway key is drained into credits with the key's
model calls, under its own route: it costs what the gateway priced it at, is
paid by whoever pays for the key, and is no model call in any count.

One search that found something is $0.000704 on the gateway (¥5 per 1,000 at
RMB 7.1/USD), so 0.0704 credits.
"""

import pytest
from sqlalchemy import select

from app.domain.agent import gateway as gw
from app.domain.agent.gateway_spend import Charge, charge_new_spend
from app.domain.agent.personal.billing import PersonalGatewayKey
from app.domain.assistant.models import AssistantGatewayKey
from app.domain.usage.ledger import Ledger, payer_for_person
from app.domain.usage.models import GATEWAY_SEARCH_MODEL, SEARCH_ROUTE, ResourceUsage
from tests.integration.conftest import registered

# The fake answers only for `gw.utc_today()`, which the drain reads too, held
# still for each test (`pinned_clock`): a run across UTC midnight cannot split
# the two reads.
pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("pinned_clock")]

SEARCH_USD = 0.000704


class OneKeysDay:
    """The gateway's daily totals for one key: two searches and a model call."""

    async def daily_spend_by_model(self, key, date):
        if key != "sk-person" or date != gw.utc_today():
            return {}
        return {
            GATEWAY_SEARCH_MODEL: gw.ModelSpend(
                GATEWAY_SEARCH_MODEL, 0, 0, 2 * SEARCH_USD, calls=2
            ),
            "deepseek-flash": gw.ModelSpend("deepseek-flash", 1000, 100, 0.01),
        }


async def test_a_search_on_a_persons_key_is_theirs_and_no_model_call(db_factory):
    async with db_factory() as session:
        user_id = await registered(session, "searcher")
        session.add(AssistantGatewayKey(user_id=user_id, key="sk-person"))
        await session.commit()
        payer = await payer_for_person(session, user_id)
        await Ledger(session).grant(payer.team_id, 10)
        await session.commit()
        before = (await Ledger(session).balance(payer)).credits_remaining

    landed = await charge_new_spend(
        db_factory,
        OneKeysDay(),
        PersonalGatewayKey(user_id),
        Charge(model="deepseek-flash", kind="assistant", user_id=user_id),
    )

    assert landed is not None
    async with db_factory() as session:
        rows = {
            r.model: r
            for r in (
                await session.scalars(
                    select(ResourceUsage).where(ResourceUsage.user_id == user_id)
                )
            ).all()
        }
        after = (await Ledger(session).balance(payer)).credits_remaining
    search = rows[GATEWAY_SEARCH_MODEL]
    assert (search.route, search.kind) == (SEARCH_ROUTE, "web_search")
    assert search.credits == pytest.approx(2 * SEARCH_USD / 0.01)
    assert rows["deepseek-flash"].route == "gateway"
    assert before - after == pytest.approx((2 * SEARCH_USD + 0.01) / 0.01)
