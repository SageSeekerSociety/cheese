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
from collections.abc import Sequence, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_roles import owner_of, standing
from app.domain.identity.services import IdentityService
from app.domain.membership.repositories import MemberRepository
from app.domain.project.models import Project, ProjectMember
from app.domain.project.repositories import ProjectRepository
from app.domain.project_permissions import Permission
from app.domain.team.models import TeamUserRelation
from app.domain.team.protocol import TeamMemberRole
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.repositories import TopicMembershipRepository
from app.domain.user.models import User


async def may_read_project(
    session: AsyncSession, *, project_id: uuid.UUID, handle: str | None
) -> bool:
    """Whether ``handle`` may read this project's conversations and summaries:
    anyone who sits in it (``app.auth.project_roles``), mentors included.

    ``handle is None`` (nobody authenticated) is False, never True: "nobody
    asked" must not read as "anybody may".
    """
    return await standing(session, project_id, handle) is not None


async def may_read_project_many(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    handles: Sequence[str],
    agents: Set[str] | None = None,
) -> dict[str, bool]:
    """``may_read_project`` for many handles, in a fixed number of reads.

    The answer is the one :func:`standing` gives handle by handle; only the
    price changes. Asking one at a time reads the project, its owner, its
    exclusions and the team relations once per person, so a room roster of
    people the project does not list costs a statement for every seat — the
    shape a 20-seat room's audience check paid ~160 statements for. Here the
    project is read once, its exclusions, its roster rows and the team
    relations once, and each handle is decided in memory.

    ``agents`` lets a caller that already asked
    :meth:`IdentityService.agents_among` for these exact handles hand the
    answer over instead of paying for the same two reads twice.
    """
    project = await ProjectRepository(session).get(project_id)
    return await _may_read_project_many(
        session, project, handles=handles, agents=agents
    )


async def _may_read_project_many(
    session: AsyncSession,
    project: Project | None,
    *,
    handles: Sequence[str],
    agents: Set[str] | None = None,
) -> dict[str, bool]:
    """``may_read_project_many`` over a project the caller has already read.

    The branches follow ``app.auth.project_roles.standing`` in order — agent,
    project owner, team owner, team admin unless excluded, then 「on the roster,
    or in the project through the team and not excluded」 — because the answer
    has to stay the same one, handle for handle.
    """
    asked = list(dict.fromkeys(handles))
    # No handle is nobody, and nobody reads nothing — the answer
    # ``may_read_project`` gives ``None``. Every handle asked about gets a key,
    # so a caller can index the answer with what it passed in.
    named = [handle for handle in asked if handle]
    if project is None or not named:
        return {handle: False for handle in asked}
    out: dict[str, bool] = {handle: False for handle in asked if not handle}
    if agents is None:
        agents = await IdentityService(session).agents_among(named)
    owner = await owner_of(session, project)
    listed = set(
        (
            await session.scalars(
                select(ProjectMember.user_handle).where(
                    ProjectMember.project_id == project.id,
                    ProjectMember.user_handle.in_(named),
                )
            )
        ).all()
    )
    excluded = await MemberRepository(session).excluded_handles(project.id)
    team_roles: dict[str, int] = {}
    if project.team_id is not None:
        rows = await session.execute(
            select(User.username, TeamUserRelation.role)
            .join(TeamUserRelation, TeamUserRelation.user_id == User.id)
            .where(
                TeamUserRelation.team_id == project.team_id,
                TeamUserRelation.deleted_at.is_(None),
                User.deleted_at.is_(None),
                User.username.in_(named),
            )
        )
        team_roles = {name: role for name, role in rows.all()}
    for handle in named:
        if handle in agents:
            # An AI teammate sits only where the project put it on the roster.
            out[handle] = handle in listed
            continue
        if handle == owner:
            out[handle] = True
            continue
        team_role = team_roles.get(handle)
        if team_role == TeamMemberRole.OWNER:
            out[handle] = True
            continue
        if team_role == TeamMemberRole.ADMIN and handle not in excluded:
            out[handle] = True
            continue
        out[handle] = handle in listed or (
            team_role is not None and handle not in excluded
        )
    return out


async def may_write_project(
    session: AsyncSession, *, project_id: uuid.UUID, handle: str | None
) -> bool:
    """Whether ``handle`` works in this project rather than looking on: their
    role lets them change its content. What exactly they may change is the one
    permission each route asks for (``project_roles.may``)."""
    found = await standing(session, project_id, handle)
    return found is not None and Permission.edit_content in found.permissions


async def outsiders_reading(
    session: AsyncSession, *, project_id: uuid.UUID, room_id: uuid.UUID
) -> list[str]:
    """The people who read this room but could not read the project themselves.

    An agent answers into the room it works in, so whatever it reads about the
    project is shown to everyone the room shows things to. Those are the
    project's own people (a room that is not private) and the room's roster
    (any room), and only a roster seat can belong to somebody outside the
    project: someone added to one channel, or still in a private chat after
    leaving the project. So the roster alone is asked, and ``may_read_project``
    is paid only for the seats the project roster and its owner leave
    unexplained — asked once for all of them (:func:`may_read_project_many`),
    not once per seat, because the roster's length is the room's to choose and
    the answer's cost must not be. A seat with no handle is nobody and reads
    nothing.
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
    people = [handle for handle in unexplained if handle not in agents]
    if not people:
        return []
    readable = await _may_read_project_many(
        session, project, handles=people, agents=agents
    )
    return [handle for handle in people if not readable[handle]]


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
