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

What the answer is: **the owner, the roster, a member of the team the project
belongs to, the 出题者 of the 赛题 the project was opened for, or a 教师 (an
admin/creator) of the 题目板 that 赛题 sits on.** The first three are exactly
the claims ``ProjectRepository.list_visible_to`` lists a project under, and
that is not a coincidence kept for its own sake - a listing and a door have to
agree. The fourth and fifth are not in that listing (a teacher's sidebar does
not want forty projects they do not work in); they are here because the
student dashboard needs to open one that a class produced.
``_is_asker_of_the_task`` explains why the fourth stops at the task's creator;
the fifth is the board's teacher, in ``app.auth.space_access``. Until
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

from app.auth.space_access import is_admin_of_projects_task
from app.domain.membership.repositories import MemberRepository
from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository
from app.domain.task.repositories import TaskRepository
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
    user = await UserRepository(session).get_by_username(handle)
    if user is None:
        return False
    if await _is_asker_of_the_task(session, project=project, user_id=user.id):
        return True
    # The board's teacher: an admin (or creator) of the 题目板 the project's
    # 赛题 sits on. The product now says a 题目板 is a course and its 教师 is
    # its 管理员 — the person who runs the board, not merely whoever set one
    # problem, and a course has many problems and several 助教. Deliberately
    # still ONE board and the people who administer it (see
    # ``app.auth.space_access``), not ``TaskVisibilityService.can_view_task``:
    # that would widen the audience with a participation row or an email
    # domain, i.e. hand a whole class of students every team's conversations.
    if await is_admin_of_projects_task(
        session, external_task_id=project.external_task_id, user_id=user.id
    ):
        return True
    # The project's OWN team, not ``team_for_project`` - that helper also folds
    # in the owner's personal team, which would let a personal team's members
    # into a project their owner never put there. Widening the claim set is a
    # different decision than consolidating three copies of it.
    if project.team_id is None:
        return False
    return await TeamRepository(session).is_team_member(project.team_id, user.id)


async def _is_asker_of_the_task(
    session: AsyncSession, *, project: Project, user_id: int
) -> bool:
    """出题者: the person who set the 赛题 this project was opened for.

    A 赛题 registration project is owned by whoever applies
    (``ProjectService.for_participation`` passes the applicant as
    ``owner_handle``), so the teacher who set the problem is a stranger to every
    project it produces - they cannot read a single one of its rooms, and the
    dashboard that is supposed to show them how their class did has nothing to
    stand on. The link that identifies them is ``Project.external_task_id`` →
    ``Task.creator_id``, and it is on the task, not on the project.

    Deliberately the TASK CREATOR only. ``TaskVisibilityService.can_view_task``
    answers a wider question that is right for a task page (space admins,
    participants, an allowed email domain) and wrong here: a class-wide domain
    allowlist would hand every student of a class the conversations of every
    team's project, which is the over-permission this module exists to close,
    just with a school's blessing. The 出题者 is not 全站教师, either - this is
    one person and the projects of one task.
    """
    if project.external_task_id is None:
        return False
    task = await TaskRepository(session).get_by_id(project.external_task_id)
    if task is None or task.creator_id is None:
        return False
    return task.creator_id == user_id


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
