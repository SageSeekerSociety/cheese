"""Charging a person for what their 芝士 spent, from the gateway's own record.

Every model call of a person's 芝士 is made on that person's key
(``keys.py``), and only while a question they asked is being answered
(``asking.py``), so what the gateway records under the key is exactly what they
asked for, priced the way the gateway prices it: cache reads at the cache rate.
After each question the key's new spend is read, as a project's is
(``agent.gateway.drain_new_usage``), and charged to the person's credits
(``kind = "assistant"``), once: the checkpoint the read moves past is the one
stored with the key, compared and advanced in the same transaction as the
charge.

The gateway writes its spend log a little after a call ends. A read that finds
nothing is tried again a few seconds later, and once more after that; whatever
lands later still is charged by the next question's read.
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.domain.agent.gateway import LlmGateway, drain_new_usage
from app.domain.assistant.models import AssistantGatewayKey
from app.domain.service_keys import gateway_base, gateway_configured
from app.domain.usage.personal import PersonalCredits

logger = logging.getLogger(__name__)


def gateway() -> LlmGateway | None:
    if not gateway_configured():
        return None
    return LlmGateway(gateway_base(), settings.llm_gateway_admin_key or "")


#: When the key's spend is read after a question: at once, then again.
ATTEMPTS_S = (0.0, 3.0, 20.0)


async def charge_new_spend(
    sessions: async_sessionmaker[AsyncSession],
    user_id: int,
    admin: LlmGateway | None = None,
) -> bool:
    """Charge what the person's key spent since it was last charged; whether
    anything was."""
    admin = admin or gateway()
    if admin is None:
        return False
    async with sessions() as session:
        row = await session.get(AssistantGatewayKey, user_id)
        if row is None:
            return False
        key, ckpt = row.key, row.usage_ckpt
    drained = await drain_new_usage(admin, key, ckpt)
    if drained is None or not drained[0]:
        return False
    spent, next_ckpt = drained
    async with sessions() as session:
        row = (
            await session.execute(
                select(AssistantGatewayKey)
                .where(AssistantGatewayKey.user_id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        if row.usage_ckpt != ckpt:
            # Another read charged this window first.
            return False
        credits = PersonalCredits(session)
        for model in spent:
            await credits.charge_spent(
                user_id,
                model=model.model or settings.assistant_model,
                input_tokens=model.prompt_tokens,
                output_tokens=model.completion_tokens,
                cost_usd=model.spend_usd,
                kind="assistant",
            )
        row.usage_ckpt = next_ckpt
        await session.commit()
    return True


async def settle(sessions: async_sessionmaker[AsyncSession], user_id: int) -> None:
    """Charge one question's spend once the gateway has recorded it."""
    for delay in ATTEMPTS_S:
        await asyncio.sleep(delay)
        try:
            if await charge_new_spend(sessions, user_id):
                return
        except Exception:  # noqa: BLE001 — the next question's read charges it
            logger.warning("charging a person's 芝士 spend failed", exc_info=True)
            return
