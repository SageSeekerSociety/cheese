"""Space Analytics view service (NT-API aligned).

Implements the first three new analytics endpoints:

- GET /spaces/{spaceId}/analytics/overview
- GET /spaces/{spaceId}/analytics/alerts
- GET /spaces/{spaceId}/analytics/publishers

Data model: loads all tasks / memberships / submissions / reviews for the space
in bulk and aggregates in-memory. For demo-scale data this is simple and fast.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError

# The status vocabulary, the `_Context` type and the pure computation live in
# `analytics_compute`; the names below are re-exported one by one (`X as X`) so
# that every existing import path keeps working.
from app.domain.space.analytics_compute import APPROVED_MAP as APPROVED_MAP
from app.domain.space.analytics_compute import (
    APPROVED_REVERSE_MAP as APPROVED_REVERSE_MAP,
)
from app.domain.space.analytics_compute import (
    INACTIVE_PUBLISHER_DAYS as INACTIVE_PUBLISHER_DAYS,
)
from app.domain.space.analytics_compute import (
    OVERDUE_SUBMISSION_DAYS as OVERDUE_SUBMISSION_DAYS,
)
from app.domain.space.analytics_compute import STALLED_TASK_DAYS as STALLED_TASK_DAYS
from app.domain.space.analytics_compute import SUBMITTED_STATUSES as SUBMITTED_STATUSES
from app.domain.space.analytics_compute import SUCCESS_STATUS as SUCCESS_STATUS
from app.domain.space.analytics_compute import (
    _Context,
    bucket_key,
    bucketize,
    build_distribution,
    compute_entity_metrics,
    compute_overview_student_metrics,
    compute_overview_trends,
    compute_publishers_rows,
    compute_task_distributions,
    compute_task_row,
    decode_identity,
    format_local_datetime,
    format_local_datetime_ms,
    participants_csv_text,
    publishers_csv_text,
    resolve_user_display_name,
    safe_ratio,
    task_completion_label,
    tasks_csv_text,
    to_timestamp_ms,
)
from app.domain.space.analytics_participant_compute import (
    compute_participant_distributions,
    compute_participant_entity_metrics,
    compute_participant_student_metrics,
    compute_participant_trends,
    filter_participant_memberships,
)
from app.domain.space.models import Space
from app.domain.space.repositories import SpaceCategoryRepository, SpaceRepository
from app.domain.task.models import (
    Task,
    TaskMembership,
    TaskSubmission,
    TaskSubmissionReview,
)
from app.domain.task.submission_state import (
    has_a_live_submission,
    has_a_passed_submission,
)
from app.domain.team.models import Team
from app.domain.user.models import User, UserProfile, UserRealNameIdentity
from app.domain.user.repositories import UserProfileRepository, UserRepository

# --- Enums / constants -------------------------------------------------------

COMPLETION_STATUSES = {
    "NOT_SUBMITTED",
    "PENDING_REVIEW",
    "REJECTED_RESUBMITTABLE",
    "FAILED",
    "SUCCESS",
}

GROUP_BY_VALUES = {"day", "week", "month"}

PUBLISHER_SORT_FIELDS = {
    "taskCount",
    "participantCount",
    "successRate",
    "lastTaskCreatedAt",
}

TASK_SORT_FIELDS = {
    "createdAt",
    "deadline",
    "participantCount",
    "submittedParticipantCount",
    "successfulParticipantCount",
    "submissionConversionRate",
    "successRate",
}

REAL_NAME_FILTERS = {"all", "with", "without"}

# Default analytics window
DEFAULT_WINDOW_DAYS = 180

# --- Service -----------------------------------------------------------------


class SpaceAnalyticsViewService:
    """Service backing the NT-aligned analytics endpoints (overview / alerts / publishers)."""  # noqa: E501

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._space_repo = SpaceRepository(session=session)
        self._category_repo = SpaceCategoryRepository(session=session)
        self._user_repo = UserRepository(session=session)
        self._profile_repo = UserProfileRepository(session=session)

    # ------------------------------------------------------------------
    # Pure computation, bound back onto the class
    # ------------------------------------------------------------------
    # The functions live in `analytics_compute` (they touch no session); these
    # aliases keep `self._x(...)` and `SpaceAnalyticsViewService._x(...)` — the
    # spelling the tests use — working exactly as before.

    _safe_ratio = staticmethod(safe_ratio)
    _to_timestamp_ms = staticmethod(to_timestamp_ms)
    _build_distribution = staticmethod(build_distribution)
    _bucket_key = staticmethod(bucket_key)
    _bucketize = staticmethod(bucketize)
    _format_local_datetime_ms = staticmethod(format_local_datetime_ms)
    _format_local_datetime = staticmethod(format_local_datetime)
    _decode_identity = staticmethod(decode_identity)
    _resolve_user_display_name = staticmethod(resolve_user_display_name)
    _task_completion_label = staticmethod(task_completion_label)
    _compute_entity_metrics = staticmethod(compute_entity_metrics)
    _compute_overview_student_metrics = staticmethod(compute_overview_student_metrics)
    _compute_task_distributions = staticmethod(compute_task_distributions)
    _compute_overview_trends = staticmethod(compute_overview_trends)
    _compute_publishers_rows = staticmethod(compute_publishers_rows)
    _compute_task_row = staticmethod(compute_task_row)

    _filter_participant_memberships = staticmethod(filter_participant_memberships)
    _compute_participant_entity_metrics = staticmethod(
        compute_participant_entity_metrics
    )
    _compute_participant_student_metrics = staticmethod(
        compute_participant_student_metrics
    )
    _compute_participant_distributions = staticmethod(compute_participant_distributions)
    _compute_participant_trends = staticmethod(compute_participant_trends)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_overview(
        self,
        *,
        space_id: int,
        from_ts: int | None,
        to_ts: int | None,
        category_id: int | None,
        publisher_id: int | None,
        task_approved: str | None,
        group_by: str,
    ) -> dict:
        group_by_norm = self._normalize_group_by(group_by)
        from_dt, to_dt = self._resolve_window(from_ts, to_ts)
        approved_value = self._parse_approved(task_approved)

        await self._ensure_space_exists(space_id)
        ctx = await self._load_context(
            space_id=space_id,
            from_dt=from_dt,
            to_dt=to_dt,
            category_id=category_id,
            publisher_id=publisher_id,
            approved_value=approved_value,
        )

        entity_metrics = self._compute_entity_metrics(ctx)
        student_metrics = self._compute_overview_student_metrics(ctx)
        task_distributions = self._compute_task_distributions(ctx)
        trends = self._compute_overview_trends(
            ctx, from_dt=from_dt, to_dt=to_dt, group_by=group_by_norm
        )

        return {
            "summary": {
                "spaceId": space_id,
                "from": self._to_timestamp_ms(from_dt),
                "to": self._to_timestamp_ms(to_dt),
            },
            "entityMetrics": entity_metrics,
            "studentMetrics": student_metrics,
            "taskDistributions": task_distributions,
            "trends": trends,
        }

    async def get_alerts(self, *, space_id: int) -> dict:
        await self._ensure_space_exists(space_id)
        # Alerts are evaluated against the *current* state of the space,
        # without any time window filtering (frontend passes no params).
        ctx = await self._load_context(
            space_id=space_id,
            from_dt=None,
            to_dt=None,
            category_id=None,
            publisher_id=None,
            approved_value=None,
        )

        now = datetime.now(UTC)

        pending_task_approval = sum(
            1 for t in ctx.tasks if t.approved == APPROVED_MAP["NONE"]
        )
        pending_participant_approval = sum(
            1 for m in ctx.memberships if m.approved == APPROVED_MAP["NONE"]
        )
        pending_submission_review = self._count_pending_submission_reviews(ctx)

        stalled_task = self._count_stalled_tasks(ctx, now=now)
        overdue_unreviewed = self._count_overdue_unreviewed_submissions(ctx, now=now)
        inactive_publisher = self._count_inactive_publishers(ctx, now=now)

        return {
            "pendingTaskApprovalCount": pending_task_approval,
            "pendingParticipantApprovalCount": pending_participant_approval,
            "pendingSubmissionReviewCount": pending_submission_review,
            "stalledTaskCount": stalled_task,
            "overdueUnreviewedSubmissionCount": overdue_unreviewed,
            "inactivePublisherCount": inactive_publisher,
        }

    async def get_publishers(
        self,
        *,
        space_id: int,
        from_ts: int | None,
        to_ts: int | None,
        category_id: int | None,
        task_approved: str | None,
        sort_by: str,
        sort_order: str,
    ) -> dict:
        from_dt, to_dt = self._resolve_window(from_ts, to_ts)
        approved_value = self._parse_approved(task_approved)
        sort_by_norm = self._normalize_publisher_sort_by(sort_by)
        sort_order_norm = self._normalize_sort_order(sort_order)

        await self._ensure_space_exists(space_id)
        ctx = await self._load_context(
            space_id=space_id,
            from_dt=from_dt,
            to_dt=to_dt,
            category_id=category_id,
            publisher_id=None,
            approved_value=approved_value,
        )

        rows = self._compute_publishers_rows(ctx)
        rows.sort(
            key=lambda row: row.get(sort_by_norm) or 0,
            reverse=(sort_order_norm == "desc"),
        )
        return {"publishers": rows}

    async def get_tasks(
        self,
        *,
        space_id: int,
        from_ts: int | None,
        to_ts: int | None,
        category_id: int | None,
        publisher_id: int | None,
        task_approved: str | None,
        has_pending_review: bool | None,
        has_pending_approval: bool | None,
        sort_by: str,
        sort_order: str,
    ) -> dict:
        from_dt, to_dt = self._resolve_window(from_ts, to_ts)
        approved_value = self._parse_approved(task_approved)
        sort_by_norm = self._normalize_task_sort_by(sort_by)
        sort_order_norm = self._normalize_sort_order(sort_order)

        await self._ensure_space_exists(space_id)
        ctx = await self._load_context(
            space_id=space_id,
            from_dt=from_dt,
            to_dt=to_dt,
            category_id=category_id,
            publisher_id=publisher_id,
            approved_value=approved_value,
        )

        rows = [self._compute_task_row(task, ctx) for task in ctx.tasks]

        # Post-filter by hasPendingReview / hasPendingApproval
        if has_pending_review is True:
            rows = [r for r in rows if r["pendingReviewCount"] > 0]
        elif has_pending_review is False:
            rows = [r for r in rows if r["pendingReviewCount"] == 0]
        if has_pending_approval is True:
            rows = [r for r in rows if r["pendingParticipantApprovalCount"] > 0]
        elif has_pending_approval is False:
            rows = [r for r in rows if r["pendingParticipantApprovalCount"] == 0]

        rows.sort(
            key=lambda row: row.get(sort_by_norm) or 0,
            reverse=(sort_order_norm == "desc"),
        )
        return {"tasks": rows}

    async def export_tasks_csv(
        self,
        *,
        space_id: int,
        from_ts: int | None,
        to_ts: int | None,
        category_id: int | None,
        publisher_id: int | None,
        task_approved: str | None,
        has_pending_review: bool | None,
        has_pending_approval: bool | None,
    ) -> str:
        data = await self.get_tasks(
            space_id=space_id,
            from_ts=from_ts,
            to_ts=to_ts,
            category_id=category_id,
            publisher_id=publisher_id,
            task_approved=task_approved,
            has_pending_review=has_pending_review,
            has_pending_approval=has_pending_approval,
            sort_by="createdAt",
            sort_order="desc",
        )
        return tasks_csv_text(data["tasks"])

    async def export_publishers_csv(
        self,
        *,
        space_id: int,
        from_ts: int | None,
        to_ts: int | None,
        category_id: int | None,
        task_approved: str | None,
    ) -> str:
        data = await self.get_publishers(
            space_id=space_id,
            from_ts=from_ts,
            to_ts=to_ts,
            category_id=category_id,
            task_approved=task_approved,
            sort_by="taskCount",
            sort_order="desc",
        )
        return publishers_csv_text(data["publishers"])

    async def get_participants(
        self,
        *,
        space_id: int,
        from_ts: int | None,
        to_ts: int | None,
        category_id: int | None,
        publisher_id: int | None,
        task_approved: str | None,
        participation_approved: str | None,
        completion_status: str | None,
        real_name: str,
        group_by: str,
    ) -> dict:
        group_by_norm = self._normalize_group_by(group_by)
        from_dt, to_dt = self._resolve_window(from_ts, to_ts)
        approved_value = self._parse_approved(task_approved)
        participation_approved_value = self._parse_approved_optional(
            participation_approved
        )
        completion_status_norm = self._normalize_completion_status(completion_status)
        real_name_norm = self._normalize_real_name_filter(real_name)

        await self._ensure_space_exists(space_id)
        ctx = await self._load_context(
            space_id=space_id,
            from_dt=from_dt,
            to_dt=to_dt,
            category_id=category_id,
            publisher_id=publisher_id,
            approved_value=approved_value,
            include_participant_details=True,
        )

        memberships = self._filter_participant_memberships(
            ctx.memberships,
            ctx=ctx,
            participation_approved=participation_approved_value,
            completion_status=completion_status_norm,
            real_name=real_name_norm,
        )

        entity_metrics = self._compute_participant_entity_metrics(memberships, ctx)
        student_metrics = self._compute_participant_student_metrics(memberships, ctx)
        distributions = self._compute_participant_distributions(memberships, ctx)
        trends = self._compute_participant_trends(
            memberships,
            ctx=ctx,
            from_dt=from_dt,
            to_dt=to_dt,
            group_by=group_by_norm,
        )

        return {
            "summary": {
                "spaceId": space_id,
                "from": self._to_timestamp_ms(from_dt),
                "to": self._to_timestamp_ms(to_dt),
            },
            "entityMetrics": entity_metrics,
            "studentMetrics": student_metrics,
            "distributions": distributions,
            "trends": trends,
        }

    async def get_people(self, *, space_id: int) -> dict:
        """逐人一行，外加「领了没动」的逐条名单 —— 都只看这块板上未删除的题。

        两处口径，页面上也照说一遍：

        - **一条领取走到哪一步**由提交与评审算（``submission_state`` 里那两句
          判据）：没有 live 提交＝在做，有 live 提交且其评审通过＝通过，有 live
          提交但没通过（还在队列里、或被打回）＝已交。不看
          ``task_membership.completion_status`` —— 那一列后端还没有人推进，
          读了只会说谎。
        - **领了没动**逐条给：领取时间早于 14 天、而且一版 live 提交都没有。14
          天与 alerts 那一格的 ``STALLED_TASK_DAYS`` 同一个数，不是另定一个。

        团队报名按**队**算一行（``is_team`` 那半就是队号），和它在领取表里的
        粒度一致：一条领取是队领的，就没有「队里哪个人领的」这回事。

        两条查询各自聚合，都在 SQL 里数完 —— 逐人那一格不做 N+1（人数 × 领取
        数会跟着板的大小一起长）。
        """
        await self._ensure_space_exists(space_id)
        now = datetime.now(UTC)
        rows = await self._load_people_rows(space_id=space_id)
        stalled_rows = await self._load_stalled_claims(space_id=space_id, now=now)

        names = await self._load_member_names(
            [(bool(r.is_team), int(r.member_id)) for r in rows]
            + [(bool(r.is_team), int(r.member_id)) for r in stalled_rows]
        )

        people = [
            {
                "userId": int(row.member_id),
                "name": names[(bool(row.is_team), int(row.member_id))],
                "isTeam": bool(row.is_team),
                "claims": int(row.claims),
                "passed": int(row.passed),
                "submitted": int(row.submitted),
                # 在做 = 交了还没判的 + 一版都没交的。两条判据都在「有没有 live
                # 提交」这一层，所以它是减出来的，不需要第三条 SQL。
                "inProgress": int(row.claims) - int(row.passed) - int(row.submitted),
            }
            for row in rows
        ]
        stalled = [
            {
                "taskId": int(row.task_id),
                "taskTitle": row.task_name,
                "userId": int(row.member_id),
                "name": names[(bool(row.is_team), int(row.member_id))],
                "isTeam": bool(row.is_team),
                "claimedAt": self._to_timestamp_ms(row.created_at),
            }
            for row in stalled_rows
        ]
        return {"people": people, "stalled": stalled}

    async def _load_people_rows(self, *, space_id: int):
        """逐人（逐队）一行：领了几条、其中通过几条、已交未通过几条。

        行按领取数降序 —— 页面照它画，不重排。同数时按 (is_team, member_id)
        定序，两次调用给同一个顺序。
        """
        claims = func.count(TaskMembership.id).label("claims")
        passed = func.sum(
            case((has_a_passed_submission(TaskMembership.id), 1), else_=0)
        ).label("passed")
        submitted = func.sum(
            case(
                (
                    and_(
                        has_a_live_submission(TaskMembership.id),
                        ~has_a_passed_submission(TaskMembership.id),
                    ),
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
        result = await self._session.execute(stmt)
        return list(result.all())

    async def _load_stalled_claims(self, *, space_id: int, now: datetime):
        """领了没动的**逐条**领取：超过 14 天、且一版 live 提交都没有。

        最老的排在最前 —— 这一格是「谁该被问一句」，放最久的先问。
        """
        threshold = now - timedelta(days=STALLED_TASK_DAYS)
        stmt = (
            select(
                TaskMembership.member_id,
                TaskMembership.is_team,
                TaskMembership.created_at,
                Task.id.label("task_id"),
                Task.name.label("task_name"),
            )
            .join(Task, Task.id == TaskMembership.task_id)
            .where(
                Task.space_id == space_id,
                Task.deleted_at.is_(None),
                TaskMembership.deleted_at.is_(None),
                TaskMembership.created_at < threshold,
                ~has_a_live_submission(TaskMembership.id),
            )
            .order_by(TaskMembership.created_at.asc(), TaskMembership.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.all())

    async def _load_member_names(
        self, members: list[tuple[bool, int]]
    ) -> dict[tuple[bool, int], str]:
        """(is_team, member_id) → 显示名。个人取昵称 → 用户名，队取队名。

        与 ``_resolve_user_display_name``（出题人那一格）同一套说法。查不到的
        兜一个占位串而不是空字符串 —— 空名字在表里像少了一个人。
        """
        personal_ids = sorted({mid for is_team, mid in members if not is_team})
        team_ids = sorted({mid for is_team, mid in members if is_team})
        users = await self._user_repo.get_by_ids(personal_ids) if personal_ids else {}
        profiles = (
            await self._profile_repo.get_profiles_by_user_ids(personal_ids)
            if personal_ids
            else {}
        )
        teams_by_id = await self._load_teams_by_id(team_ids)

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
            names[(True, team_id)] = (
                team.name if team is not None else f"Team {team_id}"
            )
        return names

    async def _load_teams_by_id(self, team_ids: list[int]) -> dict[int, Team]:
        if not team_ids:
            return {}
        stmt = select(Team).where(Team.id.in_(team_ids), Team.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return {int(team.id): team for team in result.scalars().all()}

    async def export_participants_csv(
        self,
        *,
        space_id: int,
        from_ts: int | None,
        to_ts: int | None,
        category_id: int | None,
        publisher_id: int | None,
        task_approved: str | None,
        participation_approved: str | None,
        completion_status: str | None,
        real_name: str,
    ) -> tuple[str, list[TaskMembership]]:
        """Return (csv_text, memberships) so the route can write audit logs."""
        from_dt, to_dt = self._resolve_window(from_ts, to_ts)
        approved_value = self._parse_approved(task_approved)
        participation_approved_value = self._parse_approved_optional(
            participation_approved
        )
        completion_status_norm = self._normalize_completion_status(completion_status)
        real_name_norm = self._normalize_real_name_filter(real_name)

        await self._ensure_space_exists(space_id)
        ctx = await self._load_context(
            space_id=space_id,
            from_dt=from_dt,
            to_dt=to_dt,
            category_id=category_id,
            publisher_id=publisher_id,
            approved_value=approved_value,
            include_participant_details=True,
        )

        memberships = self._filter_participant_memberships(
            ctx.memberships,
            ctx=ctx,
            participation_approved=participation_approved_value,
            completion_status=completion_status_norm,
            real_name=real_name_norm,
        )

        return participants_csv_text(memberships, ctx), memberships

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    async def _ensure_space_exists(self, space_id: int) -> None:
        result = await self._session.execute(
            select(Space.id).where(Space.id == space_id)
        )
        if result.scalar_one_or_none() is None:
            raise NotFoundError(f"Space {space_id} not found")

    async def _load_context(
        self,
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
        task_stmt = select(Task).where(
            Task.space_id == space_id, Task.deleted_at.is_(None)
        )
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
        task_rows = (await self._session.execute(task_stmt)).scalars().all()
        tasks = list(task_rows)
        task_ids = [t.id for t in tasks]

        # Memberships tied to those tasks
        memberships: list[TaskMembership] = []
        if task_ids:
            mem_stmt = select(TaskMembership).where(
                TaskMembership.task_id.in_(task_ids),
                TaskMembership.deleted_at.is_(None),
            )
            memberships = list((await self._session.execute(mem_stmt)).scalars().all())

        # Submissions tied to those memberships
        submissions: list[TaskSubmission] = []
        membership_ids = [m.id for m in memberships]
        if membership_ids:
            sub_stmt = select(TaskSubmission).where(
                TaskSubmission.membership_id.in_(membership_ids),
                TaskSubmission.deleted_at.is_(None),
            )
            submissions = list((await self._session.execute(sub_stmt)).scalars().all())

        # Reviews for those submissions
        reviews_by_submission_id: dict[int, TaskSubmissionReview] = {}
        submission_ids = [s.id for s in submissions]
        if submission_ids:
            rev_stmt = select(TaskSubmissionReview).where(
                TaskSubmissionReview.submission_id.in_(submission_ids),
                TaskSubmissionReview.deleted_at.is_(None),
            )
            review_rows = (await self._session.execute(rev_stmt)).scalars().all()
            for r in review_rows:
                # If multiple reviews exist for the same submission, keep the latest
                existing = reviews_by_submission_id.get(r.submission_id)
                if existing is None or r.updated_at > existing.updated_at:
                    reviews_by_submission_id[r.submission_id] = r

        # Categories in this space (for byCategory distribution)
        category_rows = await self._category_repo.list_categories_for_space(
            space_id=space_id, include_archived=True
        )
        categories_by_id = {c.id: c for c in category_rows}

        # Users + profiles for all creators (used in publishers endpoint)
        creator_ids = list({t.creator_id for t in tasks})
        users_by_id: dict[int, User] = {}
        profiles_by_user_id: dict[int, UserProfile] = {}
        if creator_ids:
            users_by_id = await self._user_repo.get_by_ids(creator_ids)
            profiles_by_user_id = await self._profile_repo.get_profiles_by_user_ids(
                creator_ids
            )

        # Participants-specific: identities + users for personal memberships
        identities_by_user_id: dict[int, UserRealNameIdentity] = {}
        member_users_by_user_id: dict[int, User] = {}
        if include_participant_details and memberships:
            personal_member_ids = list(
                {m.member_id for m in memberships if not m.is_team}
            )
            if personal_member_ids:
                id_stmt = select(UserRealNameIdentity).where(
                    UserRealNameIdentity.user_id.in_(personal_member_ids),
                    UserRealNameIdentity.deleted_at.is_(None),
                )
                id_rows = (await self._session.execute(id_stmt)).scalars().all()
                identities_by_user_id = {i.user_id: i for i in id_rows}
                member_users_by_user_id = await self._user_repo.get_by_ids(
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
        )

    # ------------------------------------------------------------------
    # Aggregations: alerts
    # ------------------------------------------------------------------

    def _count_pending_submission_reviews(self, ctx: _Context) -> int:
        """A submission is pending review if it has no review row yet."""
        return sum(
            1 for s in ctx.submissions if s.id not in ctx.reviews_by_submission_id
        )

    def _count_stalled_tasks(self, ctx: _Context, *, now: datetime) -> int:
        """Tasks that have at least one approved participant but no submission for 14 days."""  # noqa: E501
        threshold = now - timedelta(days=STALLED_TASK_DAYS)
        stalled = 0
        for task in ctx.tasks:
            members = ctx.memberships_by_task_id.get(task.id, [])
            approved_members = [
                m for m in members if m.approved == APPROVED_MAP["APPROVED"]
            ]
            if not approved_members:
                continue
            latest_submission: datetime | None = None
            for m in approved_members:
                for sub in ctx.submissions_by_membership_id.get(m.id, []):
                    if latest_submission is None or sub.created_at > latest_submission:
                        latest_submission = sub.created_at
            if latest_submission is None or latest_submission < threshold:
                stalled += 1
        return stalled

    def _count_overdue_unreviewed_submissions(
        self, ctx: _Context, *, now: datetime
    ) -> int:
        """Submissions waiting for review for more than 7 days."""
        threshold = now - timedelta(days=OVERDUE_SUBMISSION_DAYS)
        return sum(
            1
            for s in ctx.submissions
            if s.id not in ctx.reviews_by_submission_id and s.created_at < threshold
        )

    def _count_inactive_publishers(self, ctx: _Context, *, now: datetime) -> int:
        """Publishers that have created tasks historically but none in the last 30 days."""  # noqa: E501
        threshold = now - timedelta(days=INACTIVE_PUBLISHER_DAYS)
        latest_by_creator: dict[int, datetime] = {}
        for task in ctx.tasks:
            existing = latest_by_creator.get(task.creator_id)
            if existing is None or task.created_at > existing:
                latest_by_creator[task.creator_id] = task.created_at
        return sum(1 for latest in latest_by_creator.values() if latest < threshold)

    # ------------------------------------------------------------------
    # Helpers: parsing & utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_window(
        from_ts: int | None, to_ts: int | None
    ) -> tuple[datetime, datetime]:
        now_utc = datetime.now(UTC)
        to_dt = (
            datetime.fromtimestamp(to_ts / 1000, tz=UTC)
            if to_ts is not None
            else now_utc
        )
        from_dt = (
            datetime.fromtimestamp(from_ts / 1000, tz=UTC)
            if from_ts is not None
            else to_dt - timedelta(days=DEFAULT_WINDOW_DAYS)
        )
        if from_dt > to_dt:
            raise BadRequestError("'from' must be <= 'to'")
        return from_dt, to_dt

    @staticmethod
    def _parse_approved(value: str | None) -> int | None:
        if value is None:
            return None
        key = value.upper()
        if key not in APPROVED_MAP:
            raise BadRequestError(
                f"Invalid taskApproved: {value}. Must be APPROVED, DISAPPROVED, or NONE"
            )
        return APPROVED_MAP[key]

    @staticmethod
    def _parse_approved_optional(value: str | None) -> int | None:
        if value is None or value == "":
            return None
        key = value.upper()
        if key not in APPROVED_MAP:
            raise BadRequestError(
                f"Invalid participationApproved: {value}. Must be APPROVED, DISAPPROVED, or NONE"  # noqa: E501
            )
        return APPROVED_MAP[key]

    @staticmethod
    def _normalize_completion_status(value: str | None) -> str | None:
        if value is None or value == "":
            return None
        key = value.upper()
        if key not in COMPLETION_STATUSES:
            raise BadRequestError(
                f"Invalid completionStatus: {value}. Must be one of {sorted(COMPLETION_STATUSES)}"  # noqa: E501
            )
        return key

    @staticmethod
    def _normalize_real_name_filter(value: str) -> str:
        key = (value or "all").lower()
        if key not in REAL_NAME_FILTERS:
            raise BadRequestError(
                f"Invalid realName: {value}. Must be one of {sorted(REAL_NAME_FILTERS)}"
            )
        return key

    @staticmethod
    def _normalize_group_by(value: str) -> str:
        key = (value or "day").lower()
        if key not in GROUP_BY_VALUES:
            raise BadRequestError(
                f"Invalid groupBy: {value}. Must be day, week, or month"
            )
        return key

    @staticmethod
    def _normalize_publisher_sort_by(value: str) -> str:
        key = value or "taskCount"
        if key not in PUBLISHER_SORT_FIELDS:
            raise BadRequestError(
                f"Invalid sortBy: {value}. Must be one of {sorted(PUBLISHER_SORT_FIELDS)}"  # noqa: E501
            )
        return key

    @staticmethod
    def _normalize_task_sort_by(value: str) -> str:
        key = value or "createdAt"
        if key not in TASK_SORT_FIELDS:
            raise BadRequestError(
                f"Invalid sortBy: {value}. Must be one of {sorted(TASK_SORT_FIELDS)}"
            )
        return key

    @staticmethod
    def _normalize_sort_order(value: str) -> str:
        key = (value or "desc").lower()
        if key not in {"asc", "desc"}:
            raise BadRequestError(f"Invalid sortOrder: {value}. Must be asc or desc")
        return key
