"""Compose normalization facts with the same-session edit mention effect."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.chat import SentText
from app.domain.agent.mentions import announce_mentions
from app.domain.block.models import Block


async def announce_edited_message(
    session: AsyncSession,
    sent: SentText,
    block: Block,
    author: str,
    *,
    before: str,
) -> None:
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
