"""A session whose conversation stays behind when it starts somewhere else.

A seat's conversation lives on the computer its session runs on: the harness
keeps it in the room's home there (`device_launch`). A session placed on
another computer than the one its row last recorded, or in another generation
of its room's home, finds nothing of that conversation where it starts. Left
alone, the start would still be offered the old conversation, find nothing to
resume, and quietly begin a new one that the platform believes is the old one.

So the channel asks here when it records the new place (``host_move``, on the
row it holds locked), and the start continues nothing (`PreparedSession.retired`).
The new place keeps where the conversation is (``CONVERSATION_ON``) until the
session has started there: only then is the conversation ended and the room told
why its teammate no longer remembers what was said (``end_moved``). A start that
fails leaves both undone, and the next start sees the same move again; one that
goes back to where the conversation is continues it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.harness import SessionRef
from app.domain.agent_session.models import AgentSession
from app.domain.notice_meta import (
    EVENT_CONVERSATION_NOT_CARRIED,
    SEVERITY_INFO,
    WHO_PLATFORM,
    notice,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HostMove:
    """A conversation that runs on ``origin`` and is about to start on
    ``target``: each a (session host, room generation) pair."""

    #: The conversation the session would resume.
    token: str
    origin: tuple[str, str]
    target: tuple[str, str]


#: The key of a recorded place naming where the session's conversation is,
#: while the session has been placed away from it and has not yet started there
#: (``host_move``): a [session host, room generation] pair.
CONVERSATION_ON = "conversation_on"


def host_move(row: AgentSession, *, center: str, resource: str) -> HostMove | None:
    """Whether the session ``row`` is about to start away from its
    conversation: it has one to go on with (a token that was not given up on),
    and the session host or the generation of the room the conversation is on
    is not the one it is placed on now. None for a session that never ran
    anywhere."""
    location = row.runtime_location or {}
    if not row.resume_token or row.abandoned_at is not None or not location:
        return None
    origin = tuple(
        location.get(CONVERSATION_ON)
        or (location["device_id"], location["resource_id"])
    )
    target = (center, resource)
    if origin == target:
        return None
    return HostMove(row.resume_token, origin, target)


async def end_moved(db: AsyncSession, session: SessionRef, move: HostMove) -> None:
    """The session started away from the conversation ``move`` leaves behind:
    give that conversation up, tell the room, and stop recording where it was
    (``CONVERSATION_ON``), in the caller's transaction, on the row held locked:
    the line lands with the give-up or not at all.

    Given up only while the row still names that conversation: another backend
    placing the same session at once ended it already, and the room hears it
    once. What the conversation was told stays recorded: this turn tells the
    new one everything, and records that over it."""
    row = await db.scalar(
        select(AgentSession)
        .where(
            AgentSession.conversation_id == session.conversation_id,
            AgentSession.agent_handle == session.agent_handle,
            AgentSession.harness == session.harness,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        return
    location = dict(row.runtime_location or {})
    if location.pop(CONVERSATION_ON, None) is not None:
        row.runtime_location = location
    if row.resume_token != move.token or row.abandoned_at is not None:
        return
    row.abandoned_at = row.updated_at = datetime.now(UTC)
    logger.info(
        "session_host_moved topic=%s seat=%s from=%s to=%s outcome=new_conversation",
        session.conversation_id,
        session.agent_handle,
        "/".join(move.origin),
        "/".join(move.target),
    )
    await announce(
        db,
        place_id=session.topic_id,
        # The same computer, another generation of the room: it was reopened.
        content=say(
            "conversationNotCarriedReopened"
            if move.origin[0] == move.target[0]
            else "conversationNotCarried",
            agent=f"<@{session.agent_handle}>",
        ),
        meta={
            **notice(
                EVENT_CONVERSATION_NOT_CARRIED, severity=SEVERITY_INFO, who=WHO_PLATFORM
            ),
            # Shown beside the teammate whose conversation it is.
            "seat": session.agent_handle,
        },
        task_id=session.inner_id,
    )
