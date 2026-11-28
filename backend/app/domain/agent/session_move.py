"""A session whose conversation stays behind when it starts somewhere else.

A seat's conversation lives on the computer its session runs on: the harness
keeps it in the room's home there (`device_launch`). A session placed on
another computer than the one its row last recorded, or in another generation
of its room's home, finds nothing of that conversation where it starts. Left
alone, the start would still be offered the old conversation, find nothing to
resume, and quietly begin a new one that the platform believes is the old one.

So the channel asks here before it records the new place (``host_move``) and
carries the conversation to where the session starts (`session_carry`). When it
cannot, it ends the conversation in the same transaction (``end_moved``): the
start continues nothing (`PreparedSession.retired`), the turn's opening tells
the new conversation the chat it has not read, and the room is told why its
teammate no longer remembers what was said.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.domain.agent import session_carry
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
    #: The teammate whose seat wrote it there.
    agent: str


def host_move(row: AgentSession, *, center: str, resource: str) -> HostMove | None:
    """Whether the session ``row`` is about to start away from its
    conversation: it has one to go on with (a token that was not given up on),
    and the session host or the generation of the room it recorded is not the
    one it is placed on now. None for a session that never ran anywhere."""
    location = row.runtime_location or {}
    if not row.resume_token or row.abandoned_at is not None or not location:
        return None
    origin = (location["device_id"], location["resource_id"])
    target = (center, resource)
    if origin == target:
        return None
    agent = (location.get("runtime") or {}).get("agent_handle") or row.agent_handle
    return HostMove(row.resume_token, origin, target, agent)


#: What the room is told, by why the conversation did not come along
#: (`session_carry`): the computer it was on is away, it is over the size a
#: carry takes, it holds other members' private content, or anything else
#: stopped it.
_SAID = {
    session_carry.OFFLINE: "conversationNotCarriedOffline",
    session_carry.TOO_LARGE: "conversationNotCarriedTooLarge",
    session_carry.NOT_ALLOWED: "conversationNotCarriedPrivate",
}


async def end_moved(
    db: AsyncSession, session: SessionRef, move: HostMove, reason: str
) -> None:
    """Give up on the conversation ``move`` leaves behind, and tell the room
    why (``reason``, `session_carry`), in the caller's transaction: the line
    lands with the give-up or not at all.

    Given up only while the row still names that conversation: another backend
    placing the same session at once ended it already, and the room hears it
    once. What the conversation was told stays recorded: this turn tells the
    new one everything, and records that over it."""
    ended = await db.execute(
        update(AgentSession)
        .where(
            AgentSession.conversation_id == session.conversation_id,
            AgentSession.agent_handle == session.agent_handle,
            AgentSession.harness == session.harness,
            AgentSession.resume_token == move.token,
            AgentSession.abandoned_at.is_(None),
        )
        .values(abandoned_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    )
    # UPDATE returns a CursorResult, which has rowcount at runtime.
    if not ended.rowcount:  # type: ignore[attr-defined]
        return
    moved(session, move, reason)
    # The same computer, another generation of the room: it was reopened.
    key = (
        "conversationNotCarriedReopened"
        if move.origin[0] == move.target[0]
        else _SAID.get(reason, "conversationNotCarried")
    )
    await announce(
        db,
        place_id=session.topic_id,
        content=say(key, agent=f"<@{session.agent_handle}>"),
        meta={
            **notice(
                EVENT_CONVERSATION_NOT_CARRIED, severity=SEVERITY_INFO, who=WHO_PLATFORM
            ),
            # Shown beside the teammate whose conversation it is.
            "seat": session.agent_handle,
        },
        task_id=session.inner_id,
    )


def moved(session: SessionRef, move: HostMove, outcome: str) -> None:
    """The one log line of a move: ``outcome`` is ``carried`` or why not."""
    logger.info(
        "session_host_moved topic=%s seat=%s from=%s to=%s outcome=%s",
        session.conversation_id,
        session.agent_handle,
        "/".join(move.origin),
        "/".join(move.target),
        outcome,
    )
