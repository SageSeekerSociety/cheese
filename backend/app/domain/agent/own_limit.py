"""When a member's own Claude Code reaches its account's usage limit.

Its owner's subscription has usage windows (five hours, a week), and a session
that reaches one is refused until the window resets. That is not a failure to
fix or retry: it is like the owner's computer being away. The session says
when the window resets (``rate_limit_event``, ``resetsAt``), so the room is told
how long, and the agent is handed a note to go on at that time — a timed
delivery (`delivery/timer.py`), which outlives any process and is handed over
once.
"""

import math
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.domain.delivery.timer import deliver_at

#: Past the reset, so the first request after it is not refused again by a
#: window that resets a moment late.
_AFTER_RESET = timedelta(minutes=1)

#: What the agent is handed when its window has reset. The agent reads it, not
#: a person, as every timed delivery's content is.
RESUME_NOTE = say("ownLimitResume")


def refused_until(rate_limit: dict | None, now: datetime) -> datetime | None:
    """When the window that refused this turn resets; None when no window
    refused it or the session did not say."""
    if not isinstance(rate_limit, dict) or rate_limit.get("status") != "rejected":
        return None
    resets = rate_limit.get("resetsAt")
    if not isinstance(resets, int | float) or isinstance(resets, bool):
        return None
    when = datetime.fromtimestamp(resets, UTC)
    return max(when, now)


async def go_on_after(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    seat: str,
    until: datetime,
    now: datetime,
) -> tuple[str, int]:
    """Hand the agent a note to go on once the window resets, and say so: the
    line for the room and how many minutes it waits."""
    due = until + _AFTER_RESET
    await deliver_at(
        session,
        when=due,
        event=RESUME_NOTE,
        recipient=seat,
        conversation_id=conversation_id,
        project_id=project_id,
    )
    minutes = max(1, math.ceil((due - now).total_seconds() / 60))
    return say("ownLimitWaiting", minutes=minutes), minutes


async def after_turn(session: AsyncSession, state, result) -> str | None:
    """A member's own Claude Code refused by its account's usage window goes on
    when the window resets: the agent is handed a note for then, and the line
    for the room says how long. None when no window refused the turn."""
    now = datetime.now(UTC)
    until = refused_until(result.rate_limit, now)
    if until is None:
        return None
    line, _ = await go_on_after(
        session,
        project_id=state.project_id,
        conversation_id=state.topic_id,
        seat=state.acting_agent,
        until=until,
        now=now,
    )
    return line
