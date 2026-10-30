"""Reading the rows an analytics report is built from.

``_load_context`` is the one bulk read the four report families share: a
space's tasks under the caller's filters, and the memberships, submissions and
reviews hanging off them, with the categories, users and profiles the payloads
name. The people grid counts in SQL instead — ``_load_people_rows`` per person
or team, ``_load_stalled_claims`` per claim nobody touched — because that count
grows with the board (``get_people`` says which two facts it reads).

Each function takes the service and goes through its session and repositories,
so the service's own ``_ensure_space_exists`` / ``_load_context`` stay the
points a test patches.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, case, func, select

from app.core.errors import NotFoundError
from app.domain.space.analytics_compute import (
    APPROVED_MAP,
    STALLED_TASK_DAYS,
    _Context,
)
from app.domain.space.models import Space
from app.domain.task.claims import started_at, started_at_of
from app.domain.task.models import (
    Task,
    TaskMembership,
    TaskSubmission,
    TaskSubmissionReview,
)
from app.domain.task.submission_state import (
    COMPLETION_STATUS_SUCCESS,
    has_a_live_submission,
)
from app.domain.team.models import Team
from app.domain.user.models import User, UserProfile, UserRealNameIdentity


async def _ensure_space_exists(service, space_id: int) -> None:
    result = await service._session.execute(
        select(Space.id).where(Space.id == space_id)
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundError(f"Space {space_id} not found")


async def _load_context(
    service,
    *,
    space_id: int,
    from_dt: datetime | None,
    to_dt: datetime | None,
    category_id: int | None,
    publisher_id: int | None,
    approved_value: int | None,
    include_participant_details: bool = False,
) -> _Context:
    # Tasks for this space
    task_stmt = select(Task).where(Task.space_id == space_id, Task.deleted_at.is_(None))
    if from_dt is not None:
        task_stmt = task_stmt.where(Task.created_at >= from_dt)
    if to_dt is not None:
        task_stmt = task_stmt.where(Task.created_at <= to_dt)
    if category_id is not None:
        task_stmt = task_stmt.where(Task.category_id == category_id)
    if publisher_id is not None:
        task_stmt = task_stmt.where(Task.creator_id == publisher_id)
    if approved_value is not None:
        task_stmt = task_stmt.where(Task.approved == approved_value)
    task_rows = (await service._session.execute(task_stmt)).scalars().all()
    tasks = list(task_rows)
    task_ids = [t.id for t in tasks]

    # Memberships tied to those tasks
    memberships: list[TaskMembership] = []
    if task_ids:
        mem_stmt = select(TaskMembership).where(
            TaskMembership.task_id.in_(task_ids),
            TaskMembership.deleted_at.is_(None),
        )
        memberships = list((await service._session.execute(mem_stmt)).scalars().all())

    # Submissions tied to those memberships
    submissions: list[TaskSubmission] = []
    membership_ids = [m.id for m in memberships]
    started_at_by_membership_id = await started_at_of(service._session, membership_ids)
    if membership_ids:
        sub_stmt = select(TaskSubmission).where(
            TaskSubmission.membership_id.in_(membership_ids),
            TaskSubmission.deleted_at.is_(None),
        )
        submissions = list((await service._session.execute(sub_stmt)).scalars().all())

    # Reviews for those submissions
    reviews_by_submission_id: dict[int, TaskSubmissionReview] = {}
    submission_ids = [s.id for s in submissions]
    if submission_ids:
        rev_stmt = select(TaskSubmissionReview).where(
            TaskSubmissionReview.submission_id.in_(submission_ids),
            TaskSubmissionReview.deleted_at.is_(None),
        )
        review_rows = (await service._session.execute(rev_stmt)).scalars().all()
        for r in review_rows:
            # If multiple reviews exist for the same submission, keep the latest
            existing = reviews_by_submission_id.get(r.submission_id)
            if existing is None or r.updated_at > existing.updated_at:
                reviews_by_submission_id[r.submission_id] = r

    # Categories in this space (for byCategory distribution)
    category_rows = await service._category_repo.list_categories_for_space(
        space_id=space_id, include_archived=True
    )
    categories_by_id = {c.id: c for c in category_rows}

    # Users + profiles for all creators (used in publishers endpoint)
    creator_ids = list({t.creator_id for t in tasks})
    users_by_id: dict[int, User] = {}
    profiles_by_user_id: dict[int, UserProfile] = {}
    if creator_ids:
        users_by_id = await service._user_repo.get_by_ids(creator_ids)
        profiles_by_user_id = await service._profile_repo.get_profiles_by_user_ids(
            creator_ids
        )

    # Participants-specific: identities + users for personal memberships
    identities_by_user_id: dict[int, UserRealNameIdentity] = {}
    member_users_by_user_id: dict[int, User] = {}
    if include_participant_details and memberships:
        personal_member_ids = list({m.member_id for m in memberships if not m.is_team})
        if personal_member_ids:
            id_stmt = select(UserRealNameIdentity).where(
                UserRealNameIdentity.user_id.in_(personal_member_ids),
                UserRealNameIdentity.deleted_at.is_(None),
            )
            id_rows = (await service._session.execute(id_stmt)).scalars().all()
            identities_by_user_id = {i.user_id: i for i in id_rows}
            member_users_by_user_id = await service._user_repo.get_by_ids(
                personal_member_ids
            )

    return _Context(
        tasks=tasks,
        memberships=memberships,
        submissions=submissions,
        reviews_by_submission_id=reviews_by_submission_id,
        categories_by_id=categories_by_id,
        users_by_id=users_by_id,
        profiles_by_user_id=profiles_by_user_id,
        identities_by_user_id=identities_by_user_id,
        member_users_by_user_id=member_users_by_user_id,
        started_at_by_membership_id=started_at_by_membership_id,
    )


async def _load_people_rows(service, *, space_id: int):
    """逐人（逐队）一行：领了几条、其中通过几条、已交未通过几条。

    行按领取数降序 —— 页面照它画，不重排。同数时按 (is_team, member_id)
    定序，两次调用给同一个顺序。
    """
    claims = func.count(TaskMembership.id).label("claims")
    # 通过 = 每个阶段都通过：那条轴推出来、存在领取行上的值（`submission_state`）。
    done = TaskMembership.completion_status == COMPLETION_STATUS_SUCCESS
    passed = func.sum(case((done, 1), else_=0)).label("passed")
    submitted = func.sum(
        case(
            (
                and_(has_a_live_submission(TaskMembership.id), ~done),
                1,
            ),
            else_=0,
        )
    ).label("submitted")
    stmt = (
        select(
            TaskMembership.is_team,
            TaskMembership.member_id,
            claims,
            passed,
            submitted,
        )
        .join(Task, Task.id == TaskMembership.task_id)
        .where(
            Task.space_id == space_id,
            Task.deleted_at.is_(None),
            TaskMembership.deleted_at.is_(None),
        )
        .group_by(TaskMembership.is_team, TaskMembership.member_id)
        .order_by(
            claims.desc(),
            TaskMembership.is_team.asc(),
            TaskMembership.member_id.asc(),
        )
    )
    result = await service._session.execute(stmt)
    return list(result.all())


async def _load_stalled_claims(service, *, space_id: int, now: datetime):
    """开始做了没动的**逐条**领取：开始做（项目建好）超过 14 天、且一版 live
    提交都没有。还在等批准、批了还没开始做的，没有项目，不算。

    最老的排在最前 —— 这一格是「谁该被问一句」，放最久的先问。
    """
    threshold = now - timedelta(days=STALLED_TASK_DAYS)
    started = started_at().label("started_at")
    claims = (
        select(
            TaskMembership.id,
            TaskMembership.member_id,
            TaskMembership.is_team,
            started,
            Task.id.label("task_id"),
            Task.name.label("task_name"),
        )
        .join(Task, Task.id == TaskMembership.task_id)
        .where(
            Task.space_id == space_id,
            Task.deleted_at.is_(None),
            TaskMembership.deleted_at.is_(None),
            TaskMembership.approved == APPROVED_MAP["APPROVED"],
            ~has_a_live_submission(TaskMembership.id),
        )
        .subquery()
    )
    stmt = (
        select(
            claims.c.member_id,
            claims.c.is_team,
            claims.c.started_at,
            claims.c.task_id,
            claims.c.task_name,
        )
        .where(claims.c.started_at < threshold)
        .order_by(claims.c.started_at.asc(), claims.c.id.asc())
    )
    result = await service._session.execute(stmt)
    return list(result.all())


async def _load_member_names(
    service, members: list[tuple[bool, int]]
) -> dict[tuple[bool, int], str]:
    """(is_team, member_id) → 显示名。个人取昵称 → 用户名，队取队名。

    与 ``_resolve_user_display_name``（出题人那一格）同一套说法。查不到的
    兜一个占位串而不是空字符串 —— 空名字在表里像少了一个人。
    """
    personal_ids = sorted({mid for is_team, mid in members if not is_team})
    team_ids = sorted({mid for is_team, mid in members if is_team})
    users = await service._user_repo.get_by_ids(personal_ids) if personal_ids else {}
    profiles = (
        await service._profile_repo.get_profiles_by_user_ids(personal_ids)
        if personal_ids
        else {}
    )
    teams_by_id = await service._load_teams_by_id(team_ids)

    names: dict[tuple[bool, int], str] = {}
    for user_id in personal_ids:
        profile = profiles.get(user_id)
        user = users.get(user_id)
        if profile is not None and profile.nickname:
            names[(False, user_id)] = profile.nickname
        elif user is not None and user.username:
            names[(False, user_id)] = user.username
        else:
            names[(False, user_id)] = f"User {user_id}"
    for team_id in team_ids:
        team = teams_by_id.get(team_id)
        names[(True, team_id)] = team.name if team is not None else f"Team {team_id}"
    return names


async def _load_teams_by_id(service, team_ids: list[int]) -> dict[int, Team]:
    if not team_ids:
        return {}
    stmt = select(Team).where(Team.id.in_(team_ids), Team.deleted_at.is_(None))
    result = await service._session.execute(stmt)
    return {int(team.id): team for team in result.scalars().all()}
