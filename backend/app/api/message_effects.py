"""Compose normalization facts with the same-session edit mention effect."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import listing, say
from app.domain.agent.announce import announce
from app.domain.agent.mentions import announce_mentions
from app.domain.agent.turn.intake.rewrite import SentText
from app.domain.block.models import Block
from app.domain.notice_meta import (
    EVENT_MENTION_FUSED,
    SEVERITY_WARN,
    WHO_PLATFORM,
    notice,
)
from app.domain.thread.services import answered_in


async def announce_edited_message(
    session: AsyncSession,
    sent: SentText,
    block: Block,
    author: str,
    *,
    before: str,
) -> bool:
    """Who the new text names is told, and a teammate among them gets its turn.

    The two things sending the same text does, on the edit's own session: the
    people it newly @s are notified (`announce_mentions`), and the seats it
    names are recorded as deliveries (`record_mentions`, the ledger a message
    wakes through). Returns whether anybody new was called, so the caller can
    let the dispatcher out once the edit is committed.

    `skip` is who the text named before the edit: they were called when the
    message was posted, and the room's own agent among them is woken by the
    message rather than by a delivery, so the ledger's per-message-and-seat
    dedupe would not know about it.
    """
    assert sent.roster is not None
    await announce_mentions(
        session,
        sent.room,
        block,
        author,
        sent.roster,
        before=before,
        flag_unresolved=sent.by_agent,
    )
    # deferred-import: tests replace these names on app.domain.delivery.mention
    from app.domain.delivery.mention import (
        AGENT_MENTIONS_PER_HOUR,
        Summoned,
        mentioned_handles,
        named_seats,
        record_mentions,
    )

    # A teammate the text named before the edit was called when it was posted.
    skip = frozenset(mentioned_handles(before))
    named = await named_seats(
        session, room_id=sent.room.id, author=author, content=sent.text, skip=skip
    )
    summoned = Summoned()
    if named:
        # Where it is answered is resolved only now: a message in a channel's
        # main line gets its 支线 the moment it names somebody, and an edit that
        # names nobody must not open one.
        summoned = await record_mentions(
            session,
            project_id=block.project_id,
            room_id=sent.room.id,
            conversation_id=await answered_in(session, block),
            block_id=block.id,
            author=author,
            content=sent.text,
            quoted_context=(block.meta or {}).get("quoted_context"),
            by_agent=sent.by_agent,
            occurred_at=datetime.now(UTC),
            skip=skip,
        )
    if summoned.fused:
        # Say so rather than swallow it, exactly as the send path does: a
        # mention the hourly fuse stopped looks the same as one nobody made.
        await announce(
            session,
            place_id=sent.room.id,
            # A 支线's line belongs to the 支线; on the main line it is the room's.
            task_id=(
                None if block.conversation_id == sent.room.id else block.conversation_id
            ),
            content=say("mentionFused"),
            meta=notice(
                EVENT_MENTION_FUSED,
                severity=SEVERITY_WARN,
                who=WHO_PLATFORM,
                detail=say(
                    "mentionFusedDetail",
                    limit=AGENT_MENTIONS_PER_HOUR,
                    names=listing(summoned.fused),
                ),
            ),
        )
    return bool(summoned.woken)
