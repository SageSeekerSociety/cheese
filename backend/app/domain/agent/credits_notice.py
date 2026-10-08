"""The room line for a running turn whose model calls the credits refused.

Admission refuses a `/v1/messages` call at a place when the paying team's
credits do not admit it (#715): the main line's next step, or a 分身 it
started. The turn is stamped once and the room told why at once, in the same
words a turn refused before it started gets (`credits_event`), rather than
waiting for Claude Code's ten retries to end in `StopFailure` with a reading of
the 429 that says "Invalid API key".
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.sentences import error_frame
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent.room_events import post_system_event
from app.domain.usage.credits import CREDITS_EXHAUSTED_META, credits_event

logger = logging.getLogger(__name__)


async def note_credits_refusal(
    sessions: async_sessionmaker, topic_id: uuid.UUID, reason: str | None
) -> None:
    """Stamp the turn running at ``topic_id`` refused for credits and say
    ``reason`` (the ledger's refusal: what ran out and when it comes back) in
    the room.

    Nothing to stamp (no turn in flight at this place) is not an error: the
    turn-start refusal already covers a turn that has not begun. Never raises —
    the proxy fails OPEN on an admission error, so a bookkeeping bug here must
    not become a reason to let a refused turn through.
    """
    turn_id: uuid.UUID | None = None
    try:
        async with sessions() as session:
            repo = AgentTurnRepository(session)
            turn_id = await repo.open_turn_id_for_topic(topic_id)
            if turn_id is None:
                return
            flipped = await repo.mark_credits_refused(turn_id, datetime.now(UTC))
            if flipped:
                await session.commit()
    except Exception:  # noqa: BLE001 — see docstring
        logger.exception("could not stamp credits-refused for topic %s", topic_id)
        return
    if not flipped or turn_id is None:
        return
    line = credits_event(reason)
    try:
        payload = await post_system_event(
            sessions, topic_id, line, turn_id, meta=CREDITS_EXHAUSTED_META
        )
        if payload is not None:
            await get_broker().publish(
                str(topic_id),
                error_frame(line, type="error", persisted=True),
            )
    except Exception:  # noqa: BLE001 — see docstring
        logger.exception("could not post credits-refused notice for topic %s", topic_id)
