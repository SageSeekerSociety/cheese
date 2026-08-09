"""Webhook ingress: mint credentials, verify inbound posts, land them as Blocks.

Delivery retry mirrors app.domain.workspace.dogfood_notices — a dropped inbound
webhook is a silent hole in the topic's timeline (CI results, deploy outcomes),
so a transient DB failure is retried before giving up.
"""

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.webhook_auth import mint_webhook_token, webhook_token_claims
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.webhook.repositories import WebhookTokenRepository

logger = logging.getLogger(__name__)

_RETRY_DELAYS_SECONDS = (0, 5, 30)


async def mint(
    session: AsyncSession, *, topic_id: uuid.UUID, project_id: uuid.UUID
) -> str:
    """Mint (or rotate, if one already exists) the webhook credential for a
    topic. Returns the raw token — hand it to the caller once; it is never
    recoverable from storage afterwards."""
    version = await WebhookTokenRepository(session).bump_version(topic_id, project_id)
    return mint_webhook_token(
        project_id=str(project_id), topic_id=str(topic_id), version=version
    )


async def verify(session: AsyncSession, *, topic_id: uuid.UUID, token: str) -> bool:
    claims = webhook_token_claims(token, topic_id=str(topic_id))
    if claims is None:
        return False
    current = await WebhookTokenRepository(session).current_version(topic_id)
    return current is not None and current == claims.get("v")


async def post_with_retries(
    session_factory,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    content: str,
    source: str,
) -> bool:
    """Land an inbound webhook payload as an event Block, retrying transient DB
    failures. Returns whether it landed. Never raises."""
    last_exc: Exception | None = None
    for delay in _RETRY_DELAYS_SECONDS:
        if delay:
            await asyncio.sleep(delay)
        try:
            async with session_factory() as session:
                await BlockRepository(session).add(
                    project_id=project_id,
                    topic_id=topic_id,
                    author=source,
                    author_type=AuthorType.system,
                    content=content,
                    kind=BlockKind.event,
                    meta={"source": source},
                )
                await session.commit()
            return True
        except Exception as exc:  # noqa: BLE001 — retry, then give up loudly
            last_exc = exc
    logger.error(
        "failed to land webhook post for topic %s after %d attempts: %s",
        topic_id,
        len(_RETRY_DELAYS_SECONDS),
        last_exc,
    )
    return False
