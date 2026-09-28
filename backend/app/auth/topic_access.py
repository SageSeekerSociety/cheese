"""Whether a room admits somebody — the caller, or a person the request names.

``may_read_project`` answers the project-shaped question, and ``may_read_topic``
reads it off the project. The door a route actually stands behind is narrower,
and it is the rule in ``app.domain.authz.policy``: a seat in THIS room, or
membership in its project, and for a private room the seat alone.

The rule has to be askable about somebody who is not the one asking, because
that is what a request body does — it names a person. A card is filed to a
reviewer named in the body, and whether the room will later let that reviewer
accept it is exactly this question. Asking it in the route instead, with the
reads spelled out a second time, is how the two ends drift: the card gets filed
happily and 403s whoever it named.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_access import may_read_project
from app.domain.authz.policy import topic_admits_handle
from app.domain.topic.models import TopicRole
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.repositories import TopicMembershipRepository


async def may_act_in_topic(
    session: AsyncSession, *, project_id: uuid.UUID, topic_id: uuid.UUID, handle: str
) -> bool:
    """Whether this room would admit ``handle``, whoever that is.

    ``project_id`` is the caller's answer to "whose project is this", passed as
    ``ActorResolver.authorize_topic`` receives it; the room itself is read here
    for the one thing only it knows — whether it is private, which decides
    whether project membership counts at all. A room that is not there admits
    nobody through a seat, and project membership still answers, which is the
    behaviour every caller had before this existed.
    """
    topic = await TopicRepository(session).get(topic_id)
    members = TopicMembershipRepository(session)

    async def topic_role(tid: uuid.UUID, who: str) -> TopicRole | None:
        row = await members.get(topic_id=tid, member_handle=who)
        return row.role if row is not None else None

    async def is_project_member(pid: uuid.UUID, who: str) -> bool:
        return await may_read_project(session, project_id=pid, handle=who)

    return await topic_admits_handle(
        handle,
        project_id=project_id,
        topic_id=topic_id,
        topic_role=topic_role,
        is_project_member=is_project_member,
        is_private=bool(topic and topic.is_private),
    )
