"""A person's own key on the gateway, which every model call of their 芝士 is made on.

Minted the first time the person asks and kept in ``assistant_gateway_keys``,
with the one model it may call: the assistant's model at the time. When the
deployment's assistant model changes, the next question finds the key bound to
the old one, so a new key is issued for the new model and the old one is
revoked at the gateway — after what it spent has been charged, since the
spend that is read is the old key's.

Minting is not idempotent on the gateway, so concurrent questions across
processes serialise on an advisory lock and the loser reads the winner's key, as
the platform's own service keys do (``service_keys.service_key``).
"""

import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.gateway_spend import charge_new_spend
from app.domain.assistant.billing import PersonalGatewayKey, gateway, person_charge
from app.domain.assistant.models import AssistantGatewayKey

logger = logging.getLogger(__name__)


async def stored_key(session: AsyncSession, user_id: int) -> str | None:
    row = await session.get(AssistantGatewayKey, user_id)
    return row.key if row is not None else None


async def person_key(
    session: AsyncSession,
    user_id: int,
    sessions: async_sessionmaker[AsyncSession],
    admin: LlmGateway | None = None,
) -> str | None:
    """The person's key for the assistant model as configured now, minted (or
    re-issued) on first use and committed at once; None when there is no
    gateway or it would not mint one."""
    model = settings.assistant_model
    row = await session.get(AssistantGatewayKey, user_id)
    if row is not None and row.model == model:
        return row.key
    admin = admin or gateway()
    if admin is None:
        return None
    if row is not None:
        # The spend still on the key being replaced is charged before the key
        # and its checkpoint are, or it would never be.
        await charge_new_spend(
            sessions, admin, PersonalGatewayKey(user_id), person_charge(user_id)
        )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"assistant-gateway-key:{user_id}"},
    )
    row = (
        await session.execute(
            select(AssistantGatewayKey)
            .where(AssistantGatewayKey.user_id == user_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is not None and row.model == model:
        return row.key
    key = await admin.mint_person_key(user_id, model)
    if key is None:
        return None
    retired = row.key if row is not None else None
    if row is None:
        session.add(AssistantGatewayKey(user_id=user_id, key=key, model=model))
    else:
        row.key, row.model, row.usage_ckpt = key, model, None
    await session.commit()
    if retired is not None and not await admin.revoke_key(retired):
        logger.warning("the replaced gateway key of user %s was not revoked", user_id)
    return key
