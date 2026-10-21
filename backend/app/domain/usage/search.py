"""A web search the platform runs itself, charged in credits.

A search Claude Code's WebSearch makes is run by the model's upstream and
priced by the gateway inside the model call it rides on (``deploy/gateway/
README.md``, "Prices for web searches"). A search the platform asks a search
service for directly (``cheese_search``) rides on no model call, so it is
charged here: one usage row per search (route ``search``) and its deduction,
written together by ``Ledger.record``, to the payer of the work that asked —
the same payer that work's model calls are charged to: a room agent's search
to its project (``payer_for_project``), a person's 芝士's to that person
(``payer_for_person``). A search is admitted the way that work's model calls
are (``search_refusal``): a payer out of credits gets no search either.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.usage.credits import spend_to_credits
from app.domain.usage.ledger import Ledger, Payer, Refusal
from app.domain.usage.models import SEARCH_ROUTE

SEARCH_KIND = "web_search"
#: The service, written in the row's ``model`` column.
DEEPSEEK_SEARCH = "deepseek-search"
#: USD per successful search: $0.75 per 1,000 successful requests, the USD
#: price DeepSeek's own English page lists
#: (https://api-docs.deepseek.com/guides/search/, read 2026-10-21; the Chinese
#: page lists the CNY account's ¥5). Taken in USD because the ledger is in USD
#: and has no exchange rate, as DeepSeek's token prices are. DeepSeek
#: bills nothing for a failed, empty or timed-out search, and neither does the
#: platform: only a search that returned results is charged.
DEEPSEEK_SEARCH_USD = 0.00075


async def search_refusal(session: AsyncSession, payer: Payer) -> Refusal | None:
    """Why a search ``payer`` would pay for may not run now, or None when it
    may: the answer a model call of theirs gets (``Ledger.admit``)."""
    return await Ledger(session).admit(payer)


async def charge_search(
    session: AsyncSession,
    payer: Payer,
    *,
    topic_id: uuid.UUID | None = None,
    user_id: int | None = None,
) -> float:
    """Write and charge one successful DeepSeek search for ``payer``; the
    caller commits. Returns the credits deducted."""
    return await Ledger(session).record(
        payer,
        credits=spend_to_credits(DEEPSEEK_SEARCH_USD),
        model=DEEPSEEK_SEARCH,
        input_tokens=0,
        output_tokens=0,
        cost_usd=DEEPSEEK_SEARCH_USD,
        route=SEARCH_ROUTE,
        kind=SEARCH_KIND,
        topic_id=topic_id,
        user_id=user_id,
    )
