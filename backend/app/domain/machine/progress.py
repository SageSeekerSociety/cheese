"""Durable startup observations, separate from the turn wake-up watermark."""

import logging
import uuid

from app.core.db import async_session_factory
from app.domain.agent.announce import announce
from app.domain.block.schemas import BlockOut

logger = logging.getLogger(__name__)


async def startup_progress(
    topic_id: uuid.UUID | None, text: str, *, failed: bool = False
) -> None:
    from app.domain.agent.runtime import get_broker

    if topic_id is None:
        return
    # Observability must not turn a working machine into a failed enrollment.
    try:
        async with async_session_factory() as session:
            block = await announce(
                session,
                place_id=topic_id,
                content=text,
                meta={
                    "event_type": "cloud_startup",
                    "severity": "info",
                    "who": "platform",
                    **({"state": "failed"} if failed else {}),
                },
            )
            if block is None:
                return
            payload = BlockOut.model_validate(block).model_dump(mode="json")
            await session.commit()
        await get_broker().publish(
            str(topic_id), {"type": "event_block", "block": payload}
        )
    except Exception:
        logger.exception("Could not record cloud startup progress for %s", topic_id)
