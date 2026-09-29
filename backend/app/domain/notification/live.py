"""Who, in this process, is listening for their notices right now.

The desktop app holds one connection per signed-in person
(`app/api/routes/notifications_live.py`). When a notice that browser push would
carry (`PUSHABLE`) is committed for someone, their connections are told to look
again; they read what is new themselves, from the same query a reconnecting app
catches up with. So nothing is sent that a rolled-back transaction never wrote,
and the live path and the catch-up can never disagree about what a notice says.

The backend is one process (the chat broker, `agent/runtime.py`, is in-process
too), so an in-process registry reaches every connection.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notification.models import NotificationType
from app.domain.notification.push import PUSHABLE, push_link, push_text
from app.domain.notification.repositories import NotificationRepository

#: At most this many are handed over at once: back from a night asleep, the
#: system's notification list should not fill up.
NOTICES_AT_ONCE = 10

_listeners: dict[int, set[asyncio.Event]] = {}


@contextmanager
def listening(user_id: int) -> Iterator[asyncio.Event]:
    """An event that is set whenever `user_id` may have something new."""
    woken = asyncio.Event()
    _listeners.setdefault(user_id, set()).add(woken)
    try:
        yield woken
    finally:
        mine = _listeners.get(user_id)
        if mine is not None:
            mine.discard(woken)
            if not mine:
                del _listeners[user_id]


def wake(user_ids: Iterable[int]) -> None:
    for user_id in user_ids:
        for woken in _listeners.get(user_id, ()):
            woken.set()


def wake_after_commit(session: AsyncSession, user_ids: set[int]) -> None:
    """Wake these people's connections once `session` commits, not before."""
    if not user_ids:
        return
    event.listen(
        session.sync_session, "after_commit", lambda _s: wake(user_ids), once=True
    )


async def notices_after(db: AsyncSession, user_id: int, after: int | None) -> dict:
    """What browser push would have said to `user_id` since notice `after`, newest
    `NOTICES_AT_ONCE` of them, unread only; and where that leaves off. With no
    `after`, only where "now" is: a first connection is shown nothing from before.
    """
    repo = NotificationRepository(db)
    latest = await repo.latest_id_for_user(user_id, PUSHABLE)
    if after is None:
        return {"latest": latest, "items": []}
    items = []
    for row in await repo.pushable_after(user_id, PUSHABLE, after, NOTICES_AT_ONCE):
        payload = row.metadata_payload or {}
        title, body = push_text(NotificationType(row.type), payload)
        items.append(
            {"id": row.id, "title": title, "body": body, "url": push_link(payload)}
        )
    return {"latest": max(latest or after, after), "items": items}
