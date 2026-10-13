"""Frames for the pages open on a conversation, sent once the session commits.

Whatever a session writes that a page should see at once — a line said in a
room, a task that changed state — is queued in `session.info` under
`SHOW_ONCE_COMMITTED` with the channel it goes out on. After the commit the
listener in `domain.agent.live_notices` publishes each one; a rollback drops
them, so a page never shows what the database does not hold.

Infrastructure, not a domain: any domain that changes what a page shows queues
here, and none of them needs the realtime broker to do it.
"""

from sqlalchemy.ext.asyncio import AsyncSession

#: The `session.info` slot: (channel, frame) pairs to publish after commit.
SHOW_ONCE_COMMITTED = "notices_shown_once_committed"


def show_once_committed(session: AsyncSession, channel: object, frame: dict) -> None:
    """Send ``frame`` to the pages open on ``channel`` once this commits."""
    session.info.setdefault(SHOW_ONCE_COMMITTED, []).append((str(channel), frame))
