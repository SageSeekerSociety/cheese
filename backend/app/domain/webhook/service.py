"""Webhook ingress: mint credentials, verify inbound posts, land them as Blocks.

Two layers, deliberately separate (review #190): mint()/verify() are the
token-auth layer that only the HTTP door (app.api.routes.webhooks) calls;
post_with_retries() is the internal shared "land a system post" function with
no auth of its own — any trusted in-process caller (the HTTP route after it
verifies, or the merge-result-back-to-room card) invokes it directly.

What it adds over `announce()` is the retry and its own session per attempt:
delivery is retried (0s, 5s, 30s) before giving up — a dropped inbound webhook
is a silent hole in the topic's timeline (CI results, deploy outcomes), so a
transient DB failure must not be the end of it.
"""

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.webhook_auth import mint_webhook_token, webhook_token_claims
from app.domain.agent.announce import announce
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
    topic_id: uuid.UUID,
    content: str,
    source: str,
    meta: dict | None = None,
) -> bool:
    """Internal shared entrypoint for landing a system-authored post into a
    topic's timeline, retrying transient DB failures. Returns whether it
    landed. Never raises.

    Deliberately does NOT call verify() or touch any token — that check lives
    only in the HTTP layer (app.api.routes.webhooks.receive_webhook), which
    calls this AFTER authenticating the caller. An in-process caller (the
    "merge 后结果回房间" card) already knows its own topic_id from context and
    is trusted by construction, so it calls this function directly and skips
    the HTTP hop and the webhook-token check entirely — that check is for the
    external HTTP door, not a gate this function itself enforces.

    Each attempt gets its own session, which is the point of this wrapper: the
    outcome it reports already happened elsewhere (a merge on GitHub, a CI run),
    so the room has to learn about it even when the caller's own transaction is
    about to roll back. Everything it posts is room-only — an outcome nobody was
    named for. A notice that knows whose turn it now is names them, and naming
    them means committing with whatever changed hands, so it calls `announce`
    on the caller's own session instead of through this retry.
    """
    last_exc: Exception | None = None
    for delay in _RETRY_DELAYS_SECONDS:
        if delay:
            await asyncio.sleep(delay)
        try:
            async with session_factory() as session:
                block = await announce(
                    session,
                    place_id=topic_id,
                    content=content,
                    meta={"source": source, **(meta or {})},
                    author=source,
                )
                if block is None:
                    logger.error(
                        "dropping %s post: topic %s no longer exists",
                        source,
                        topic_id,
                    )
                    return False
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
