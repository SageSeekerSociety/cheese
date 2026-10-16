"""Space Analytics view service (NT-API aligned).

Implements the first three new analytics endpoints:

- GET /spaces/{spaceId}/analytics/overview
- GET /spaces/{spaceId}/analytics/alerts
- GET /spaces/{spaceId}/analytics/publishers

Data model: loads all tasks / memberships / submissions / reviews for the space
in bulk and aggregates in-memory. For demo-scale data this is simple and fast.

This module is the facade the callers keep using — the routes through
``space_deps``, and the tests through the class. The work sits in sibling
modules, one per reason to change:

- ``analytics_view_params``   — what the query string may say
- ``analytics_view_queries``  — reading the rows a report is built from
- ``analytics_view_alerts``   — what each alert number counts
- ``analytics_view_reports``  — assembling the payload each endpoint answers

Every method below is a one-line delegation to the module function of the same
name, or a ``staticmethod`` alias of it, so ``self._x(...)``,
``SpaceAnalyticsViewService._x(...)`` and instance patching all work as before.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

# The status vocabulary, the `_Context` type and the pure computation live in
# `analytics_compute`; the names below are re-exported one by one (`X as X`) so
# that every existing import path keeps working.
from app.domain.space import analytics_view_alerts as alerts
from app.domain.space import analytics_view_params as params
from app.domain.space import analytics_view_queries as queries
from app.domain.space import analytics_view_reports as reports
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
    resolve_user_display_name,
    safe_ratio,
    task_completion_label,
    to_timestamp_ms,
)
from app.domain.space.analytics_participant_compute import (
    compute_participant_distributions,
    compute_participant_entity_metrics,
    compute_participant_student_metrics,
    compute_participant_trends,
    filter_participant_memberships,
)
from app.domain.space.repositories import SpaceCategoryRepository, SpaceRepository
from app.domain.task.models import TaskMembership
from app.domain.team.models import Team
from app.domain.user.repositories import UserProfileRepository, UserRepository

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
    # Request parameters and the alert numbers, bound the same way
    # ------------------------------------------------------------------

    _resolve_window = staticmethod(params._resolve_window)
    _parse_approved = staticmethod(params._parse_approved)
    _parse_approved_optional = staticmethod(params._parse_approved_optional)
    _normalize_completion_status = staticmethod(params._normalize_completion_status)
    _normalize_real_name_filter = staticmethod(params._normalize_real_name_filter)
    _normalize_group_by = staticmethod(params._normalize_group_by)
    _normalize_publisher_sort_by = staticmethod(params._normalize_publisher_sort_by)
    _normalize_task_sort_by = staticmethod(params._normalize_task_sort_by)
    _normalize_sort_order = staticmethod(params._normalize_sort_order)

    _count_pending_task_approvals = staticmethod(alerts._count_pending_task_approvals)
    _count_pending_participant_approvals = staticmethod(
        alerts._count_pending_participant_approvals
    )
    _count_pending_submission_reviews = staticmethod(
        alerts._count_pending_submission_reviews
    )
    _count_stalled_tasks = staticmethod(alerts._count_stalled_tasks)
    _count_overdue_unreviewed_submissions = staticmethod(
        alerts._count_overdue_unreviewed_submissions
    )
    _count_inactive_publishers = staticmethod(alerts._count_inactive_publishers)

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
        return await reports.get_overview(
            self,
            space_id=space_id,
            from_ts=from_ts,
            to_ts=to_ts,
            category_id=category_id,
            publisher_id=publisher_id,
            task_approved=task_approved,
            group_by=group_by,
        )

    async def get_alerts(self, *, space_id: int) -> dict:
        return await reports.get_alerts(self, space_id=space_id)

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
        return await reports.get_publishers(
            self,
            space_id=space_id,
            from_ts=from_ts,
            to_ts=to_ts,
            category_id=category_id,
            task_approved=task_approved,
            sort_by=sort_by,
            sort_order=sort_order,
        )

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
        return await reports.get_tasks(
            self,
            space_id=space_id,
            from_ts=from_ts,
            to_ts=to_ts,
            category_id=category_id,
            publisher_id=publisher_id,
            task_approved=task_approved,
            has_pending_review=has_pending_review,
            has_pending_approval=has_pending_approval,
            sort_by=sort_by,
            sort_order=sort_order,
        )

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
        return await reports.export_tasks_csv(
            self,
            space_id=space_id,
            from_ts=from_ts,
            to_ts=to_ts,
            category_id=category_id,
            publisher_id=publisher_id,
            task_approved=task_approved,
            has_pending_review=has_pending_review,
            has_pending_approval=has_pending_approval,
        )

    async def export_publishers_csv(
        self,
        *,
        space_id: int,
        from_ts: int | None,
        to_ts: int | None,
        category_id: int | None,
        task_approved: str | None,
    ) -> str:
        return await reports.export_publishers_csv(
            self,
            space_id=space_id,
            from_ts=from_ts,
            to_ts=to_ts,
            category_id=category_id,
            task_approved=task_approved,
        )

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
        return await reports.get_participants(
            self,
            space_id=space_id,
            from_ts=from_ts,
            to_ts=to_ts,
            category_id=category_id,
            publisher_id=publisher_id,
            task_approved=task_approved,
            participation_approved=participation_approved,
            completion_status=completion_status,
            real_name=real_name,
            group_by=group_by,
        )

    async def get_people(self, *, space_id: int) -> dict:
        return await reports.get_people(self, space_id=space_id)

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
        return await reports.export_participants_csv(
            self,
            space_id=space_id,
            from_ts=from_ts,
            to_ts=to_ts,
            category_id=category_id,
            publisher_id=publisher_id,
            task_approved=task_approved,
            participation_approved=participation_approved,
            completion_status=completion_status,
            real_name=real_name,
        )

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    async def _ensure_space_exists(self, space_id: int) -> None:
        return await queries._ensure_space_exists(self, space_id)

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
        return await queries._load_context(
            self,
            space_id=space_id,
            from_dt=from_dt,
            to_dt=to_dt,
            category_id=category_id,
            publisher_id=publisher_id,
            approved_value=approved_value,
            include_participant_details=include_participant_details,
        )

    async def _load_people_rows(self, *, space_id: int):
        return await queries._load_people_rows(self, space_id=space_id)

    async def _load_stalled_claims(self, *, space_id: int, now: datetime):
        return await queries._load_stalled_claims(self, space_id=space_id, now=now)

    async def _load_member_names(
        self, members: list[tuple[bool, int]]
    ) -> dict[tuple[bool, int], str]:
        return await queries._load_member_names(self, members)

    async def _load_teams_by_id(self, team_ids: list[int]) -> dict[int, Team]:
        return await queries._load_teams_by_id(self, team_ids)
