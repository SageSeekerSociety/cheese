"""Frames for the pages open on a conversation, sent once the session commits.

Whatever a session writes that a page should see at once — a line said in a
room, a task that changed state — is queued in `session.info` under
`SHOW_ONCE_COMMITTED` with the channel it goes out on. After the commit the
listener in `domain.agent.live_notices` publishes each one; a rollback drops
them, so a page never shows what the database does not hold.

A released savepoint is not the session committing: another connection still
reads the rows as they were. So the queue is kept in one bucket per savepoint
(`stage`); a rollback drops the bucket of the savepoint that rolled back,
together with any savepoint opened inside it, and what is still queued goes out
when the commit that ends the session's transaction lands (`take_committed`).

Infrastructure, not a domain: any domain that changes what a page shows queues
here, and none of them needs the realtime broker to do it.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

#: The `session.info` slot: (channel, frame) pairs to publish after commit.
SHOW_ONCE_COMMITTED = "notices_shown_once_committed"

#: What a slot holds: one bucket per savepoint, oldest first. ``None`` is the
#: bucket of the frames staged outside any savepoint — the session's own level.
Buckets = dict[SessionTransaction | None, list[Any]]


def show_once_committed(session: AsyncSession, channel: object, frame: dict) -> None:
    """Send ``frame`` to the pages open on ``channel`` once this commits."""
    stage(session.sync_session, SHOW_ONCE_COMMITTED, (str(channel), frame))


def stage(session: Session, slot: str, item: Any) -> None:
    """Queue ``item`` under ``slot``, in the bucket of the savepoint it is in.

    A released savepoint hands its frames to the transaction around it by
    staying put: they go out with that transaction's commit, and go nowhere if
    it rolls back. The same item staged twice in one savepoint is queued once —
    the flush hooks run again on every flush of the same row.
    """
    buckets: Buckets = session.info.setdefault(slot, {})
    bucket = buckets.setdefault(session.get_nested_transaction(), [])
    if item not in bucket:
        bucket.append(item)


def take_committed(session: Session, slot: str) -> list[Any]:
    """Everything queued under ``slot``, for the commit that ends the transaction.

    Empty while a savepoint is being released: that commits nothing, so what is
    queued waits for the commit that ends the session's transaction.
    """
    if session.in_nested_transaction():
        return []
    buckets: Buckets = session.info.pop(slot, None) or {}
    return [item for frames in buckets.values() for item in frames]


def drop_rolled_back(session: Session, slot: str) -> None:
    """Forget what the savepoint that rolled back queued, and only that.

    Another savepoint's frames stay: releasing one commits nothing, so they are
    still waiting for the session's own commit to answer for them.
    """
    buckets: Buckets | None = session.info.get(slot)
    if not buckets:
        return
    rolled_back = session.get_nested_transaction()
    if rolled_back is None:
        session.info.pop(slot, None)  # the session's own transaction rolled back
        return
    for bucket in [b for b in buckets if _inside(b, rolled_back)]:
        del buckets[bucket]
    if not buckets:
        session.info.pop(slot, None)


def _inside(bucket: SessionTransaction | None, savepoint: SessionTransaction) -> bool:
    """Whether ``bucket`` is ``savepoint`` or a savepoint opened inside it.

    Rolling a savepoint back undoes its own writes and the writes of every
    savepoint opened inside it, released or not, so their frames go with it.
    """
    here = bucket
    while here is not None:
        if here is savepoint:
            return True
        here = here.parent
    return False
