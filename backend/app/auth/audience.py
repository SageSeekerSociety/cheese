"""What an agent may read is bounded by who will read its answer.

A person reads a room for themselves. An agent reads it to answer somewhere,
and whatever it read can end up in that answer, in front of everyone the answer
is shown to. So the question for an agent is not only "is it seated in that
room" but "may everyone its answer reaches read that room too". A room passes
when it is:

- the room the agent answers in;
- a public room, and everyone the answer reaches may read the project (a room
  with someone from outside the project in it gets no public room either);
- a private room whose seats include every person the answer reaches.

An answer with no room of its own (a project credential working from outside
the platform) reaches the project's people, so only public rooms pass.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_access import outsiders_reading
from app.domain.identity.services import IdentityService
from app.domain.topic.models import Topic, is_private_chat
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.repositories import TopicMembershipRepository


def seats_only(topic: Topic | None) -> bool:
    """Whether only the room's own seats may read it: a private chat (the one
    place the access rules ask), or a private channel."""
    return bool(topic and (is_private_chat(topic) or topic.members_only))


async def _people_seated(session: AsyncSession, room_id: uuid.UUID) -> set[str]:
    seats = {
        m.member_handle
        for m in await TopicMembershipRepository(session).list_for_topic(room_id)
        if m.member_handle
    }
    agents = await IdentityService(session).agents_among(list(seats))
    return seats - set(agents)


async def rooms_its_audience_may_read(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    answer_room: uuid.UUID | None,
    room_ids: Sequence[uuid.UUID],
) -> set[uuid.UUID]:
    """Which of ``room_ids`` an agent answering in ``answer_room`` may read."""
    rooms = {t.id: t for t in await TopicRepository(session).get_many(set(room_ids))}
    if answer_room is None:
        return {rid for rid, room in rooms.items() if not seats_only(room)}
    origin = rooms.get(answer_room) or await TopicRepository(session).get(answer_room)
    if origin is None:
        return set()
    # Each of the answer room's two facts is read only when a room other than
    # the answer room asks it: an agent reading the room it answers in, the
    # usual case, pays for neither (four statements for the public one).
    public_ok: bool | None = None
    audience: set[str] | None = None
    allowed: set[uuid.UUID] = set()
    for rid, room in rooms.items():
        if rid == answer_room:
            allowed.add(rid)
        elif not seats_only(room):
            if public_ok is None:
                public_ok = not await outsiders_reading(
                    session, project_id=project_id, room_id=answer_room
                )
            if public_ok:
                allowed.add(rid)
        elif seats_only(origin):
            if audience is None:
                audience = await _people_seated(session, answer_room)
            if audience <= await _people_seated(session, rid):
                allowed.add(rid)
    return allowed
