"""One poll advances an accept card at a time, without holding the card.

A webhook and the reconciliation clock may observe the same card, and only one
of them may advance it or send its notifications. That used to be a
``FOR UPDATE SKIP LOCKED`` held for the whole poll, which kept a database
connection checked out while the forge answered — the poll spends most of its
time waiting on GitHub. A claim says the same thing in a row the poll writes in
a short transaction and clears when it is done, so the poll can give its
connection back before every call to the forge. A poll that dies leaves its
claim to lapse after ``CLAIM_FOR``.
"""

import inspect
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.review.models import AcceptCard

#: Longer than a whole poll (each forge call times out at 30 s), short enough
#: that a poll killed mid-way (a deploy) leaves its card only briefly unpolled.
CLAIM_FOR = timedelta(minutes=5)


async def claim(session: AsyncSession, card_id: uuid.UUID) -> uuid.UUID | None:
    """Claim the card for this poll and commit; None when another poll holds it."""
    token = uuid.uuid4()
    now = datetime.now(UTC)
    claimed = await session.scalar(
        update(AcceptCard)
        .where(
            AcceptCard.id == card_id,
            or_(
                AcceptCard.poll_claimed_until.is_(None),
                AcceptCard.poll_claimed_until < now,
            ),
        )
        .values(poll_claim=token, poll_claimed_until=now + CLAIM_FOR)
        .returning(AcceptCard.id)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return token if claimed is not None else None


async def release(session: AsyncSession, card_id: uuid.UUID, token: uuid.UUID) -> None:
    """Clear this poll's claim, in the session's current transaction."""
    await session.execute(
        update(AcceptCard)
        .where(AcceptCard.id == card_id, AcceptCard.poll_claim == token)
        .values(poll_claim=None, poll_claimed_until=None)
        .execution_options(synchronize_session=False)
    )


class CommitsBeforeRemote:
    """A forge client whose every call first commits the poll's transaction,
    so no database connection waits on the forge's answer. The claim, not a
    lock, is what keeps a second poll off the card meanwhile."""

    def __init__(self, inner, session: AsyncSession) -> None:
        self._inner = inner
        self._session = session

    def __getattr__(self, name: str):
        attr = getattr(self._inner, name)
        if not inspect.iscoroutinefunction(attr):
            return attr

        async def call(*args, **kwargs):
            await self._session.commit()
            return await attr(*args, **kwargs)

        return call
