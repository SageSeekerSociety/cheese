"""Charging a person for what their 芝士 spent, from the gateway's own record.

Every model call of a person's 芝士 is made on that person's key
(``keys.py``), and only while a question they asked is being answered
(``service.take_conversation``), so what the gateway records under the key
is exactly what they asked for, priced the way the gateway prices it: cache
reads at the cache rate.
After each question the key's new spend is read and charged the way a
project's is (``agent.gateway_spend``), to the person's credits
(``kind = "assistant"``), once: the checkpoint the read moves past is the one
stored with the key, compared and advanced in the same transaction as the
charge.

The gateway writes its spend log a little after a call ends. A read that finds
nothing is tried again a few seconds later, and once more after that; whatever
lands later still is charged by the next question's read.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.domain.agent import gateway_spend
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.gateway_spend import Charge
from app.domain.assistant.models import AssistantGatewayKey
from app.domain.service_keys import gateway_base, gateway_configured
from app.domain.usage.ledger import Payer, payer_for_person


def gateway() -> LlmGateway | None:
    if not gateway_configured():
        return None
    return LlmGateway(gateway_base(), settings.llm_gateway_admin_key or "")


#: After a question its spend is read at once, then again after each of these
#: waits while nothing has landed.
RETRY_AFTER_S = (3.0, 20.0)


@dataclass(frozen=True)
class PersonalGatewayKey:
    """A person's key, kept with its checkpoint in ``assistant_gateway_keys``."""

    user_id: int

    async def _row(
        self, session: AsyncSession, *, for_update: bool
    ) -> AssistantGatewayKey | None:
        if not for_update:
            return await session.get(AssistantGatewayKey, self.user_id)
        return (
            await session.execute(
                select(AssistantGatewayKey)
                .where(AssistantGatewayKey.user_id == self.user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def read(
        self, session: AsyncSession, *, for_update: bool = False
    ) -> tuple[str, dict | None] | None:
        row = await self._row(session, for_update=for_update)
        return None if row is None else (row.key, row.usage_ckpt)

    async def advance(self, session: AsyncSession, checkpoint: dict) -> None:
        row = await self._row(session, for_update=False)
        assert row is not None
        row.usage_ckpt = checkpoint

    async def payer(self, session: AsyncSession) -> Payer:
        return await payer_for_person(session, self.user_id)


def person_charge(user_id: int) -> Charge:
    return Charge(model=settings.assistant_model, kind="assistant", user_id=user_id)


async def settle(sessions: async_sessionmaker[AsyncSession], user_id: int) -> None:
    """Charge one question's spend once the gateway has recorded it."""
    await gateway_spend.settle(
        sessions,
        gateway(),
        PersonalGatewayKey(user_id),
        person_charge(user_id),
        retry_after=RETRY_AFTER_S,
    )
