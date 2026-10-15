"""Who, in this process, is listening for their notices right now.

Two kinds of connection listen (`app/api/routes/notifications_live.py`). The
desktop app holds one per signed-in person and is told about a notice browser
push would carry (`PUSHABLE`); a browser holds one per person and is told about
any new row in the inbox, so the feed it shows can say there is something new.
Either way the connection is only told to look again; it reads what is new
itself, from the same query a reconnecting one catches up with. So nothing is
sent that a rolled-back transaction never wrote, and the live path and the
catch-up can never disagree about what a notice says.

The backend is one process (the chat broker, `agent/runtime.py`, is in-process
too), so an in-process registry reaches every connection. During a deploy two
slots run side by side, and a commit made in the other one wakes nobody here:
that connection is at most one beat late, because each beat compares its cursor
with the newest notice in the database (`notifications_live.py`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import after_own_commit
from app.domain.notification.models import NotificationType
from app.domain.notification.push import PUSHABLE, away_text, push_link, push_text
from app.domain.notification.repositories import NotificationRepository
from app.domain.user.services import languages_by_ids

#: At most this many are handed over at once: back from a night asleep, the
#: system's notification list should not fill up.
NOTICES_AT_ONCE = 10

#: Connections waiting for a pushable notice (the desktop app).
_pushable: dict[int, set[asyncio.Event]] = {}
#: Connections waiting for any new row in the inbox (a browser's feed).
_inbox: dict[int, set[asyncio.Event]] = {}


@contextmanager
def listening(user_id: int, *, inbox: bool = False) -> Iterator[asyncio.Event]:
    """An event that is set whenever `user_id` may have a new pushable notice,
    or, with `inbox`, any new row in their inbox."""
    registry = _inbox if inbox else _pushable
    woken = asyncio.Event()
    registry.setdefault(user_id, set()).add(woken)
    try:
        yield woken
    finally:
        mine = registry.get(user_id)
        if mine is not None:
            mine.discard(woken)
            if not mine:
                del registry[user_id]


def wake(registry: dict[int, set[asyncio.Event]], user_ids: Iterable[int]) -> None:
    for user_id in user_ids:
        for woken in registry.get(user_id, ()):
            woken.set()


def wake_after_commit(
    session: AsyncSession, *, inbox: set[int], pushable: set[int]
) -> None:
    """Wake these people's connections once `session` commits, not before:
    `inbox` got a new row, `pushable` got one browser push would carry."""
    if not inbox and not pushable:
        return

    def woken() -> None:
        wake(_inbox, inbox)
        wake(_pushable, pushable)

    after_own_commit(session, woken)


async def latest_in_inbox(db: AsyncSession, user_id: int) -> int | None:
    """The newest row in `user_id`'s inbox, of any type."""
    return await NotificationRepository(db).latest_id_for_user(user_id)


async def behind(db: AsyncSession, user_id: int, cursor: int) -> bool:
    """Whether a pushable notice past `cursor` is already committed.

    The same query `notices_after` opens with, on its own. A connection's beat
    asks it, for the notice a commit in the other backend slot never woke it
    for (`api/routes/notifications_live.py`).
    """
    latest = await NotificationRepository(db).latest_id_for_user(user_id, PUSHABLE)
    return latest is not None and latest > cursor


async def notices_after(db: AsyncSession, user_id: int, after: int | None) -> dict:
    """What browser push would have said to `user_id` since notice `after`, newest
    `NOTICES_AT_ONCE` of them, unread only; and where that leaves off. With no
    `after`, only where "now" is: a first connection is shown nothing from before.

    Said in the person's language, read each time: switching it on the page
    changes the next notice, with no reconnect. ``away`` is the one line the app
    shows instead when several came while it was away.
    """
    repo = NotificationRepository(db)
    latest = await repo.latest_id_for_user(user_id, PUSHABLE)
    if after is None:
        return {"latest": latest, "items": []}
    locale = (await languages_by_ids(db, [user_id])).get(user_id)
    items = []
    for row in await repo.pushable_after(user_id, PUSHABLE, after, NOTICES_AT_ONCE):
        payload = row.metadata_payload or {}
        title, body = push_text(NotificationType(row.type), payload, locale)
        items.append(
            {"id": row.id, "title": title, "body": body, "url": push_link(payload)}
        )
    frame: dict = {"latest": max(latest or after, after), "items": items}
    if items:
        frame["away"] = away_text(len(items), locale)
    return frame
