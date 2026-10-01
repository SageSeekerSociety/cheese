"""A person's own key on the gateway, which every model call of their 芝士 is made on.

Minted the first time the person asks and kept in ``assistant_gateway_keys``.
Minting is not idempotent on the gateway, so concurrent first questions across
processes serialise on an advisory lock and the loser reads the winner's key, as
the platform's own service keys do (``service_keys.service_key``).
"""

import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.agent.gateway import LlmGateway
from app.domain.assistant.models import AssistantGatewayKey
from app.domain.service_keys import gateway_base, gateway_configured

logger = logging.getLogger(__name__)


def gateway() -> LlmGateway | None:
    if not gateway_configured():
        return None
    return LlmGateway(gateway_base(), settings.llm_gateway_admin_key or "")


async def stored_key(session: AsyncSession, user_id: int) -> str | None:
    row = await session.get(AssistantGatewayKey, user_id)
    return row.key if row is not None else None


async def person_key(
    session: AsyncSession, user_id: int, admin: LlmGateway | None = None
) -> str | None:
    """The person's key, minted on first use and committed at once; None when
    there is no gateway or it would not mint one."""
    key = await stored_key(session, user_id)
    if key is not None:
        return key
    admin = admin or gateway()
    if admin is None:
        return None
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
    if row is not None:
        return row.key
    key = await admin.mint_person_key(user_id, settings.assistant_model)
    if key is None:
        return None
    session.add(AssistantGatewayKey(user_id=user_id, key=key))
    await session.commit()
    return key
