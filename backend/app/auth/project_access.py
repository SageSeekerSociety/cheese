"""Who may read a project's conversations - one answer, in one place.

A project's conversations are its topics, and around them sit a great many
other reads: a room's transcript, the live terminal, the accept cards a room
rides on, and the AI-derived aggregates (contributions, overview, a member's
summary). Each is a different route. Each one has to answer the same question,
and before this module existed they answered it three separate ways -
``app.api.auth.ActorResolver._is_project_member``,
``app.auth.caller.may_access_project`` and ``app.api.proxy.may_view_topic`` -
which had already drifted apart once (see below). An answer that lives in three
places is three answers.

What the answer is: **the owner, the roster, or a member of the team the
project belongs to.** Those are exactly the three claims
``ProjectRepository.list_visible_to`` lists a project under, and that is not a
coincidence kept for its own sake - a listing and a door have to agree. Until
2026-09-04 two of the three copies accepted only the first two, so a teammate
saw the project in their sidebar and on the team page, clicked in, and got 403
from every room behind it. Measured on dev: a member who had accepted a team
invitation minutes earlier got 200 on ``/projects/{id}`` and 403 on
``/topics?project_id=``.

Team membership is keyed by user id while every other authorization key here is
the handle string, so the handle is resolved to its user rather than trusting a
token's numeric id - a session token carries none (``app.auth.caller`` and
``_recover_numeric_handle`` both explain why that distinction keeps biting).

Deliberately NOT here: whether the platform enforces this at all. That is the
``authz_enforce_topic_access`` kill-switch in ``app.api.auth``, and it stays on
the routes' side of the line - this module answers a question about a person and
a project, and an operator disabling enforcement must not read as "everybody is
a member".
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.membership.repositories import MemberRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.team.repositories import TeamRepository
from app.domain.topic.repositories import TopicRepository
from app.domain.user.repositories import UserRepository


async def may_read_project(
    session: AsyncSession, *, project_id: uuid.UUID, handle: str | None
) -> bool:
    """Whether ``handle`` may read this project's conversations and summaries.

    ``handle is None`` (nobody authenticated) is False, never True: "nobody
    asked" must not read as "anybody may".
    """
    if not handle:
        return False
    if await MemberRepository(session).get(project_id=project_id, user_handle=handle):
        return True
    project = await ProjectRepository(session).get(project_id)
    if project is None:
        return False
    if project.owner_handle == handle:
        return True
    # The project's OWN team, not ``team_for_project`` - that helper also folds
    # in the owner's personal team, which would let a personal team's members
    # into a project their owner never put there. Widening the claim set is a
    # different decision than consolidating three copies of it.
    if project.team_id is None:
        return False
    user = await UserRepository(session).get_by_username(handle)
    if user is None:
        return False
    return await TeamRepository(session).is_team_member(project.team_id, user.id)


async def may_read_topic(
    session: AsyncSession, *, topic_id: uuid.UUID, handle: str | None
) -> bool:
    """Whether ``handle`` may read this room's conversation.

    A room has no roster of its own to consult: ``TopicMembership`` narrows who
    is *in* a room, but the project is what decides who may look at it at all -
    see ``authorize_topic_access``, which reads the project's roster for exactly
    this reason. A topic that does not exist is readable by nobody.
    """
    topic = await TopicRepository(session).get(topic_id)
    if topic is None or topic.project_id is None:
        return False
    return await may_read_project(session, project_id=topic.project_id, handle=handle)
