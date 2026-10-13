"""What a project's pages hear about rooms they are not looking at.

A project's frame — the channel list in its sidebar, the unread marks on each
room, the levels a person set — changes when someone else does something
anywhere in the project. A page subscribes to its project on the rooms socket
(`project:<id>`, `api/routes/chat.py`), is told when one of those things
changed, and reads it again.

A frame says what changed and where, never the new state:
``{"type": "state", "resource": <what>, "id": <room>}``. The channel row
carries the reader's own tasks and the unread marks are per person, so one
frame every member hears cannot carry them; each page asks for its own.

``resource`` is one of:

- ``topics``: the room's row in the channel list (its name, its state, its
  tasks, whether 芝士 is at work there, when it was last active);
- ``unread``: the unread marks of the project's rooms and tasks;
- ``private_unread``: the unread counts of private chats (keyed by the other
  person, not by room: a private chat is not in the channel list);
- ``notify_levels``: the levels the reader set on their rooms.

A room only its people see — a private chat, a members-only channel — is never
named to anyone else: its frames go to the people seated in it (`audience`,
read here and stripped by the socket), and nobody else learns that it exists or
that something happened in it. A change only one person's page cares about
(they read a room, set a level) goes to that person alone.

Publishing reads the room's project and who may hear of it, on a session of
its own, after the change committed. While no page in this process watches a
project, nothing is read.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from app.core.background import spawn
from app.core.obs import get_logger
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.realtime.subscriber_queue import Frame
from app.domain.block.models import Block, BlockKind

_log = get_logger("cheesex.project_feed")

#: The broker channel a project's pages hear on.
PREFIX = "project:"

TOPICS = "topics"
UNREAD = "unread"
PRIVATE_UNREAD = "private_unread"
NOTIFY_LEVELS = "notify_levels"

# The room a conversation is in, its project, and whether only its people see
# it. Bare SQL: this module sits under the broker, below the domains whose
# tables it reads.
_PLACE = text("""
    SELECT t.id, t.project_id, t.is_private, t.members_only
    FROM topics t
    WHERE t.id = coalesce(
        (SELECT room_id FROM tasks WHERE id = :conversation),
        (SELECT room_id FROM threads WHERE id = :conversation),
        :conversation
    )
""")
_SEATED = text("SELECT member_handle FROM topic_memberships WHERE topic_id = :room")


#: Where `tell_project` reads a room's project and people: the chat service's
#: sessions, handed over by the first page that subscribes (`watched_by`).
#: Until one does, nobody is listening and nothing is read.
_sessions: async_sessionmaker[AsyncSession] | None = None


def channel_of(project_id: uuid.UUID | str) -> str:
    return f"{PREFIX}{project_id}"


def watched_by(sessions: async_sessionmaker[AsyncSession]) -> None:
    """A page subscribed to a project; read for it on these sessions."""
    global _sessions
    _sessions = sessions


async def tell_project(
    conversation_id: uuid.UUID,
    *resources: str,
    only: Collection[str] | None = None,
    also: Collection[str] = (),
    everyone: bool = False,
    project_id: uuid.UUID | None = None,
) -> None:
    """Tell the pages watching the project of ``conversation_id`` (a room, a
    task or a 支线) that these ``resources`` of its room changed.

    ``only``: the people whose pages care; everyone who may see the room when
    left out. ``also``: people who just lost their seat in a room only its
    people see, whose list still shows it. ``everyone``: the room was visible
    to the whole project a moment ago (it was just made private), so naming it
    to everyone tells nobody anything new. ``project_id``, when the caller knows
    it, spares the read when nobody watches that project. Call it once the
    change is committed: a page told before the commit reads the old state and
    is not told again."""
    broker = get_broker()
    sessions = _sessions
    if (
        sessions is None
        or not broker.any_subscribed(PREFIX)
        or (
            project_id is not None
            and not broker.has_subscribers(channel_of(project_id))
        )
    ):
        return
    try:
        async with sessions() as db:
            place = (
                await db.execute(_PLACE, {"conversation": conversation_id})
            ).first()
            if place is None:
                return
            room, project, private_chat, members_only = place
            channel = channel_of(project)
            if not broker.has_subscribers(channel):
                return
            audience = set(only) if only is not None else None
            if (private_chat or members_only) and not everyone:
                seated = set((await db.execute(_SEATED, {"room": room})).scalars())
                seated |= set(also)
                audience = seated if audience is None else audience & seated
    except Exception:
        # A missed frame leaves a page one change behind until its next read;
        # it must not fail the request whose change this was.
        _log.exception("project_feed_failed", conversation=str(conversation_id))
        return
    if audience is not None and not audience:
        return
    for resource in resources:
        if private_chat and resource == UNREAD:
            resource = PRIVATE_UNREAD
        if private_chat and resource == TOPICS:
            # Not in the channel list.
            continue
        frame: Frame = {"type": "state", "resource": resource, "id": str(room)}
        if audience is not None:
            frame["audience"] = sorted(audience)
        await broker.publish(channel, frame)


#: The `session.info` slot: (conversation, resources, only) to tell once the
#: session commits.
_ONCE_COMMITTED = "project_feed_once_committed"


def tell_project_once_committed(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *resources: str,
    only: Collection[str] | None = None,
) -> None:
    """`tell_project`, once ``session`` commits; nothing if it rolls back."""
    session.sync_session.info.setdefault(_ONCE_COMMITTED, []).append(
        (conversation_id, resources, only, None)
    )


@event.listens_for(Session, "after_flush")
def _said(session: Session, _context) -> None:
    """A message stored anywhere — a channel, a task, a 支线, by a person or by
    芝士, through whichever path wrote it — moves its room up the list and
    changes someone's unread marks."""
    said = {
        (block.conversation_id, block.project_id)
        for block in session.new
        if isinstance(block, Block) and block.kind == BlockKind.message
    }
    if said:
        pending = session.info.setdefault(_ONCE_COMMITTED, [])
        for conversation, project_id in said:
            pending.append((conversation, (TOPICS, UNREAD), None, project_id))


@event.listens_for(Session, "after_commit")
def _tell_committed(session: Session) -> None:
    told: set[tuple] = set()
    for conversation, resources, only, project_id in session.info.pop(
        _ONCE_COMMITTED, ()
    ):
        key = (conversation, resources, frozenset(only) if only is not None else None)
        if key in told:
            continue
        told.add(key)
        spawn(
            tell_project(conversation, *resources, only=only, project_id=project_id),
            name="project feed",
        )


@event.listens_for(Session, "after_rollback")
def _drop_rolled_back(session: Session) -> None:
    session.info.pop(_ONCE_COMMITTED, None)


def _busy_changed(channel: str) -> None:
    try:
        conversation = uuid.UUID(channel)
    except ValueError:
        return
    spawn(tell_project(conversation, TOPICS), name="project feed")


get_broker().busy_changed.append(_busy_changed)
