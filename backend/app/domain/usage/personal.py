"""Personal credits: what one person may spend on the AI they ask for outside
any project (#2233).

Every person gets ``settings.personal_credits_monthly`` credits each calendar
month, in the platform's timezone; what is left when the month ends lapses. The
month's grant is written the first time it is needed, so nobody who never asks
has a row, and two first requests racing each other still write one.

Only a call the person asked for is charged here: a question they sent, not
work the platform does on its own. The charge is priced at the gateway's rate
for the model that answered, so an expensive model costs more credits than a
cheap one for the same tokens, and prompt tokens the provider served from its
cache cost what the gateway bills for them, not the full input price. With no
rate for that model, or no price per credit configured, there is no way to
charge correctly, and the call is refused rather than charged at a flat token
rate.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.usage.credits import usage_to_credits
from app.domain.usage.models import ComputeGrant
from app.domain.usage.repositories import UsageRepository

# Where a month starts and ends for the people using the platform.
_TZ = ZoneInfo("Asia/Shanghai")


def month_of(moment: datetime) -> date:
    local = moment.astimezone(_TZ)
    return date(local.year, local.month, 1)


def resets_at(month: date) -> datetime:
    """When the month's credits lapse and the next month's become available."""
    following = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return datetime(following.year, following.month, 1, tzinfo=_TZ)


@dataclass(frozen=True)
class Balance:
    month: date
    credits_total: float
    credits_used: float

    @property
    def credits_remaining(self) -> float:
        return self.credits_total - self.credits_used

    @property
    def resets_at(self) -> datetime:
        return resets_at(self.month)

    def exhausted_message(self) -> str:
        at = self.resets_at
        return f"本月的芝士额度已用完，{at.month}月{at.day}日重置。"


@dataclass(frozen=True)
class Rates:
    """USD per token for the model a call will use: fresh prompt tokens, output
    tokens, prompt tokens read from the provider's cache, and prompt tokens
    written to it."""

    input: float
    output: float
    cache_read: float
    cache_write: float

    def cost_usd(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """What the gateway bills for a call, the way it bills it:
        ``input_tokens`` counts every prompt token, cached ones included, and
        the cached share is billed at its own rate."""
        fresh = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
        return (
            fresh * self.input
            + cache_read_tokens * self.cache_read
            + cache_write_tokens * self.cache_write
            + output_tokens * self.output
        )

    @classmethod
    def of(
        cls, model: str, table: Mapping[str, tuple[float, float, float, float]] | None
    ) -> "Rates | None":
        """``model``'s rates from the gateway's rate table
        (``feature_stats.pricing.model_rates``); None when a call to it cannot
        be charged."""
        if not settings.llm_gateway_credit_usd or not table or model not in table:
            return None
        return cls(*table[model])


class PersonalCredits:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _grant(self, user_id: int) -> ComputeGrant:
        month = month_of(datetime.now(UTC))
        await self._session.execute(
            insert(ComputeGrant)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                month=month,
                credits_total=settings.personal_credits_monthly,
                credits_used=0.0,
            )
            .on_conflict_do_nothing(
                index_elements=["user_id", "month"],
                index_where=text("user_id IS NOT NULL"),
            )
        )
        stmt = (
            select(ComputeGrant)
            .where(ComputeGrant.user_id == user_id, ComputeGrant.month == month)
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def balance(self, user_id: int) -> Balance:
        grant = await self._grant(user_id)
        assert grant.month is not None
        return Balance(grant.month, grant.credits_total, grant.credits_used)

    async def charge(
        self,
        user_id: int,
        *,
        model: str,
        rates: Rates,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        kind: str,
    ) -> float:
        """Record one asked-for call and deduct what it cost. The balance may go
        below zero by this one call: it was admitted with credits left, and the
        tokens it spent are spent either way. Returns the credits deducted."""
        cost = rates.cost_usd(
            input_tokens, output_tokens, cache_read_tokens, cache_write_tokens
        )
        row = await UsageRepository(self._session).add(
            project_id=None,
            user_id=user_id,
            topic_id=None,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            kind=kind,
            route="gateway",
        )
        credits = usage_to_credits(row, spend_priced=True)
        grant = await self._grant(user_id)
        # An increment, not a write of a value read earlier: two answers
        # settling at once must both be deducted.
        await self._session.execute(
            update(ComputeGrant)
            .where(ComputeGrant.id == grant.id)
            .values(credits_used=ComputeGrant.credits_used + credits)
        )
        return credits
