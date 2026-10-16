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

What the answer is: **anyone who sits in the project** — its owner, an admin,
a member or a mentor, as ``app.auth.project_roles.standing`` places them. The
出题者 of the 赛题 a project was opened for and the admins of its space are not
in it unless the project made them mentors (#3830, #945): setting a problem
does not open the projects that answer it. Until 2026-09-04 three copies of
this rule had drifted apart, so a teammate saw the project in their sidebar and
on the team page, clicked in, and got 403 from every room behind it.

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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_roles import standing
from app.domain.identity.services import IdentityService
from app.domain.project.models import ProjectMember
from app.domain.project.repositories import ProjectRepository
from app.domain.project_permissions import Seat
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.repositories import TopicMembershipRepository


async def may_read_project(
    session: AsyncSession, *, project_id: uuid.UUID, handle: str | None
) -> bool:
    """Whether ``handle`` may read this project's conversations and summaries:
    anyone who sits in it (``app.auth.project_roles``), mentors included.

    ``handle is None`` (nobody authenticated) is False, never True: "nobody
    asked" must not read as "anybody may".
    """
    return await standing(session, project_id, handle) is not None


async def may_write_project(
    session: AsyncSession, *, project_id: uuid.UUID, handle: str | None
) -> bool:
    """Whether ``handle`` works in this project rather than looking on: everyone
    who sits in it but a mentor. What exactly they may change is the one
    permission each route asks for (``project_roles.may``)."""
    found = await standing(session, project_id, handle)
    return found is not None and found.seat is not Seat.mentor


async def outsiders_reading(
    session: AsyncSession, *, project_id: uuid.UUID, room_id: uuid.UUID
) -> list[str]:
    """The people who read this room but could not read the project themselves.

    An agent answers into the room it works in, so whatever it reads about the
    project is shown to everyone the room shows things to. Those are the
    project's own people (a room that is not private) and the room's roster
    (any room), and only a roster seat can belong to somebody outside the
    project: someone added to one channel, or still in a private chat after
    leaving the project. So the roster alone is asked, and the full answer of
    ``may_read_project`` is paid only for the seats the project roster and its
    owner leave unexplained. A seat with no handle is nobody and reads nothing.
    """
    seats = [
        m.member_handle
        for m in await TopicMembershipRepository(session).list_for_topic(room_id)
        if m.member_handle
    ]
    if not seats:
        return []
    project = await ProjectRepository(session).get(project_id)
    listed = set(
        (
            await session.scalars(
                select(ProjectMember.user_handle).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_handle.in_(seats),
                )
            )
        ).all()
    )
    owner = project.owner_handle if project is not None else None
    unexplained = [h for h in seats if h not in listed and h != owner]
    if not unexplained:
        return []
    agents = await IdentityService(session).agents_among(unexplained)
    return [
        handle
        for handle in unexplained
        if handle not in agents
        and not await may_read_project(session, project_id=project_id, handle=handle)
    ]


async def may_read_topic(
    session: AsyncSession, *, topic_id: uuid.UUID, handle: str | None
) -> bool:
    """Whether ``handle`` may read this room's conversation.

    A room has no roster of its own to consult: ``TopicMembership`` narrows who
    is *in* a room, but the project is what decides who may look at it at all -
    see ``authorize_topic_access``, which reads the project's roster for exactly
    this reason — except a private channel, which only its people read. A
    topic that does not exist is readable by nobody.
    """
    topic = await TopicRepository(session).get(topic_id)
    if topic is None or topic.project_id is None:
        return False
    if topic.members_only:
        return handle is not None and topic.id in await TopicMembershipRepository(
            session
        ).roles_for_member([topic.id], handle)
    return await may_read_project(session, project_id=topic.project_id, handle=handle)
