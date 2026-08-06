"""Space Analytics view service (NT-API aligned).

Implements the first three new analytics endpoints:

- GET /spaces/{spaceId}/analytics/overview
- GET /spaces/{spaceId}/analytics/alerts
- GET /spaces/{spaceId}/analytics/publishers

Data model: loads all tasks / memberships / submissions / reviews for the space
in bulk and aggregates in-memory. For demo-scale data this is simple and fast.
"""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_text
from app.core.errors import BadRequestError, NotFoundError
from app.domain.space.models import Space, SpaceCategory
from app.domain.space.repositories import SpaceCategoryRepository, SpaceRepository
from app.domain.task.models import (
    Task,
    TaskMembership,
    TaskSubmission,
    TaskSubmissionReview,
)
from app.domain.user.models import User, UserProfile, UserRealNameIdentity
from app.domain.user.repositories import UserProfileRepository, UserRepository

# --- Enums / constants -------------------------------------------------------

APPROVED_MAP: dict[str, int] = {"APPROVED": 0, "DISAPPROVED": 1, "NONE": 2}
APPROVED_REVERSE_MAP: dict[int, str] = {v: k for k, v in APPROVED_MAP.items()}

COMPLETION_STATUSES = {
    "NOT_SUBMITTED",
    "PENDING_REVIEW",
    "REJECTED_RESUBMITTABLE",
    "FAILED",
    "SUCCESS",
}
SUBMITTED_STATUSES = {"PENDING_REVIEW", "REJECTED_RESUBMITTABLE", "FAILED", "SUCCESS"}
SUCCESS_STATUS = "SUCCESS"

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

# Alert thresholds (aligned with frontend Alerts.vue copy)
STALLED_TASK_DAYS = 14
OVERDUE_SUBMISSION_DAYS = 7
INACTIVE_PUBLISHER_DAYS = 30

# Default analytics window
DEFAULT_WINDOW_DAYS = 180


# --- Context -----------------------------------------------------------------


@dataclass
class _Context:
    """In-memory aggregate holding everything the analytics endpoints need."""

    tasks: list[Task]
    memberships: list[TaskMembership]
    submissions: list[TaskSubmission]
    reviews_by_submission_id: dict[int, TaskSubmissionReview]
    categories_by_id: dict[int, SpaceCategory]
    users_by_id: dict[int, User]
    profiles_by_user_id: dict[int, UserProfile]

    # Indexed views used by multiple aggregations
    tasks_by_id: dict[int, Task] = field(default_factory=dict)
    memberships_by_task_id: dict[int, list[TaskMembership]] = field(
        default_factory=dict
    )
    submissions_by_membership_id: dict[int, list[TaskSubmission]] = field(
        default_factory=dict
    )

    # Populated only when include_participant_details=True (participants endpoints).
    identities_by_user_id: dict[int, UserRealNameIdentity] = field(default_factory=dict)
    member_users_by_user_id: dict[int, User] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.tasks_by_id = {t.id: t for t in self.tasks}
        self.memberships_by_task_id = defaultdict(list)
        for m in self.memberships:
            self.memberships_by_task_id[m.task_id].append(m)
        self.submissions_by_membership_id = defaultdict(list)
        for s in self.submissions:
            self.submissions_by_membership_id[s.membership_id].append(s)


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
        header = [
            "Task ID",
            "Task Title",
            "Category",
            "Rank",
            "Creator",
            "Created At",
            "Deadline",
            "Total Participants",
            "Approved",
            "Rejected",
            "Pending",
            "Submitted",
            "Pending Review",
            "Completed",
            "Failed",
            "Task Status",
        ]
        lines = [self._csv_row(*header)]
        for task in data["tasks"]:
            lines.append(
                self._csv_row(
                    task["taskId"],
                    task["taskName"],
                    task["category"]["name"],
                    "",  # Rank column: aligned with NT (always empty in NT too)
                    task["publisher"]["name"],
                    self._format_local_datetime_ms(task["createdAt"]),
                    self._format_local_datetime_ms(task["deadline"]),
                    task["participantCount"],
                    task["approvedParticipantCount"],
                    task["rejectedParticipantCount"],
                    task["pendingParticipantApprovalCount"],
                    task["submittedParticipantCount"],
                    task["pendingReviewCount"],
                    task["successfulParticipantCount"],
                    task["failedParticipantCount"],
                    task["approved"],
                )
            )
        # UTF-8 BOM so Excel auto-detects encoding on Windows.
        return "\ufeff" + "\n".join(lines) + "\n"

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
        header = [
            "Publisher ID",
            "Publisher Name",
            "Total Tasks",
            "Total Participants",
            "Approved Participants",
            "Submitted Participants",
            "Successful Participants",
            "Average Participants Per Task",
            "Submission Conversion Rate",
            "Success Rate",
            "Last Task Created At",
        ]
        lines = [self._csv_row(*header)]
        for p in data["publishers"]:
            lines.append(
                self._csv_row(
                    p["publisherId"],
                    p["publisherName"],
                    p["taskCount"],
                    p["participantCount"],
                    p["approvedParticipantCount"],
                    p["submittedParticipantCount"],
                    p["successfulParticipantCount"],
                    p["avgParticipantsPerTask"],
                    p["submissionConversionRate"],
                    p["successRate"],
                    self._format_local_datetime_ms(p["lastTaskCreatedAt"]),
                )
            )
        # UTF-8 BOM so Excel auto-detects encoding on Windows.
        return "\ufeff" + "\n".join(lines) + "\n"

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

        header = [
            "Task ID",
            "Task Title",
            "Category",
            "Task Rank",
            "Task Creator",
            "Created At",
            "Deadline",
            "Member ID",
            "Username",
            "Real Name",
            "Student ID",
            "Grade",
            "Major",
            "Class",
            "Phone",
            "Email",
            "Apply Reason",
            "Reject Reason",
            "Approval Status",
            "Completion Status",
            "Is Team",
            "Join Date",
        ]
        lines = [self._csv_row(*header)]
        for m in memberships:
            task = ctx.tasks_by_id.get(m.task_id)
            if task is None:
                continue
            category = ctx.categories_by_id.get(task.category_id)
            category_name = category.name if category else ""
            # NT uses creator.username directly (no nickname fallback).
            creator_user = ctx.users_by_id.get(task.creator_id)
            creator_name = creator_user.username if creator_user else ""
            identity = (
                ctx.identities_by_user_id.get(m.member_id) if not m.is_team else None
            )
            id_fields = (
                self._decode_identity(identity)
                if identity
                else {
                    "realName": "",
                    "studentId": "",
                    "grade": "",
                    "major": "",
                    "className": "",
                }
            )
            # NT hard-codes an empty string for the Username column.
            username = ""
            approval_status = APPROVED_REVERSE_MAP.get(m.approved, "NONE")
            task_rank = task.rank if task.rank is not None else ""
            lines.append(
                self._csv_row(
                    task.id,
                    task.name,
                    category_name,
                    task_rank,
                    creator_name,
                    self._format_local_datetime(task.created_at),
                    self._format_local_datetime(task.deadline),
                    m.member_id,
                    username,
                    id_fields["realName"],
                    id_fields["studentId"],
                    id_fields["grade"],
                    id_fields["major"],
                    id_fields["className"],
                    m.phone or "",
                    m.email or "",
                    "",  # Apply Reason: PY TaskMembership has no such column
                    "",  # Reject Reason: PY TaskMembership has no such column
                    approval_status,
                    m.completion_status,
                    "true" if m.is_team else "false",
                    self._format_local_datetime(m.created_at),
                )
            )
        csv_text = "\ufeff" + "\n".join(lines) + "\n"
        return csv_text, memberships

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
    # Aggregations: overview
    # ------------------------------------------------------------------

    def _compute_entity_metrics(self, ctx: _Context) -> dict:
        # NT-aligned: participantCount = memberships.size (no dedup).
        # submittedCount is based on whether a submission exists (not completion_status).  # noqa: E501
        # successRate denominator is total memberships (not submitted).
        task_count = len(ctx.tasks)
        publisher_count = len({t.creator_id for t in ctx.tasks})
        participant_count = len(ctx.memberships)

        approved_count = sum(
            1 for m in ctx.memberships if m.approved == APPROVED_MAP["APPROVED"]
        )
        submitted_count = sum(
            1 for m in ctx.memberships if ctx.submissions_by_membership_id.get(m.id)
        )
        success_count = sum(
            1 for m in ctx.memberships if m.completion_status == SUCCESS_STATUS
        )

        participation_rate = self._safe_ratio(approved_count, len(ctx.memberships))
        submission_rate = self._safe_ratio(submitted_count, approved_count)
        success_rate = self._safe_ratio(success_count, len(ctx.memberships))

        return {
            "taskCount": task_count,
            "publisherCount": publisher_count,
            "participantCount": participant_count,
            "approvedParticipantCount": approved_count,
            "submittedParticipantCount": submitted_count,
            "successfulParticipantCount": success_count,
            "participationConversionRate": participation_rate,
            "submissionConversionRate": submission_rate,
            "successRate": success_rate,
        }

    def _compute_task_distributions(self, ctx: _Context) -> dict:
        # byCategory
        category_counter: Counter[str] = Counter()
        for task in ctx.tasks:
            cat = ctx.categories_by_id.get(task.category_id)
            label = cat.name if cat else f"Category {task.category_id}"
            category_counter[label] += 1

        # byApprovalStatus
        approval_counter: Counter[str] = Counter(
            APPROVED_REVERSE_MAP.get(task.approved, "NONE") for task in ctx.tasks
        )

        # byCompletionStatus: label each task by how its members completed.
        completion_counter: Counter[str] = Counter()
        for task in ctx.tasks:
            members = ctx.memberships_by_task_id.get(task.id, [])
            completion_counter[self._task_completion_label(members)] += 1

        return {
            "byCategory": self._build_distribution("Task Categories", category_counter),
            "byApprovalStatus": self._build_distribution(
                "Task Approval Status", approval_counter
            ),
            "byCompletionStatus": self._build_distribution(
                "Participant Completion Status", completion_counter
            ),
        }

    @staticmethod
    def _task_completion_label(members: list[TaskMembership]) -> str:
        if not members:
            return "NO_PARTICIPANTS"
        statuses = {m.completion_status for m in members}
        if statuses == {SUCCESS_STATUS}:
            return "ALL_SUCCESS"
        if SUCCESS_STATUS in statuses:
            return "PARTIAL_SUCCESS"
        if statuses & SUBMITTED_STATUSES:
            return "IN_PROGRESS"
        return "NOT_STARTED"

    def _compute_overview_trends(
        self,
        ctx: _Context,
        *,
        from_dt: datetime,
        to_dt: datetime,
        group_by: str,
    ) -> dict:
        tasks_series = self._bucketize(
            (t.created_at for t in ctx.tasks),
            from_dt=from_dt,
            to_dt=to_dt,
            group_by=group_by,
        )
        participants_series = self._bucketize(
            (m.created_at for m in ctx.memberships),
            from_dt=from_dt,
            to_dt=to_dt,
            group_by=group_by,
        )
        submissions_series = self._bucketize(
            (s.created_at for s in ctx.submissions),
            from_dt=from_dt,
            to_dt=to_dt,
            group_by=group_by,
        )
        successes_series = self._bucketize(
            (
                m.updated_at
                for m in ctx.memberships
                if m.completion_status == SUCCESS_STATUS
            ),
            from_dt=from_dt,
            to_dt=to_dt,
            group_by=group_by,
        )
        return {
            "tasksCreated": tasks_series,
            "participantsJoined": participants_series,
            "submissionsCreated": submissions_series,
            "successesAchieved": successes_series,
        }

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
    # Aggregations: publishers
    # ------------------------------------------------------------------

    def _compute_publishers_rows(self, ctx: _Context) -> list[dict]:
        tasks_by_creator: dict[int, list[Task]] = defaultdict(list)
        for task in ctx.tasks:
            tasks_by_creator[task.creator_id].append(task)

        rows: list[dict] = []
        for creator_id, tasks in tasks_by_creator.items():
            task_ids = {t.id for t in tasks}
            members = [m for m in ctx.memberships if m.task_id in task_ids]

            participant_count = len({(m.member_id, m.is_team) for m in members})
            approved_count = sum(
                1 for m in members if m.approved == APPROVED_MAP["APPROVED"]
            )
            submitted_count = sum(
                1 for m in members if m.completion_status in SUBMITTED_STATUSES
            )
            success_count = sum(
                1 for m in members if m.completion_status == SUCCESS_STATUS
            )

            task_count = len(tasks)
            avg_participants = (
                round(participant_count / task_count, 4) if task_count > 0 else 0.0
            )
            submission_rate = self._safe_ratio(submitted_count, approved_count)
            success_rate = self._safe_ratio(success_count, submitted_count)
            last_created_at = max(t.created_at for t in tasks)

            rows.append(
                {
                    "publisherId": creator_id,
                    "publisherName": self._resolve_user_display_name(ctx, creator_id),
                    "taskCount": task_count,
                    "participantCount": participant_count,
                    "approvedParticipantCount": approved_count,
                    "submittedParticipantCount": submitted_count,
                    "successfulParticipantCount": success_count,
                    "avgParticipantsPerTask": avg_participants,
                    "submissionConversionRate": submission_rate,
                    "successRate": success_rate,
                    "lastTaskCreatedAt": self._to_timestamp_ms(last_created_at) or 0,
                }
            )
        return rows

    def _compute_task_row(self, task: Task, ctx: _Context) -> dict:
        members = ctx.memberships_by_task_id.get(task.id, [])
        participant_count = len(members)
        pending_approval = sum(1 for m in members if m.approved == APPROVED_MAP["NONE"])
        approved = sum(1 for m in members if m.approved == APPROVED_MAP["APPROVED"])
        rejected = sum(1 for m in members if m.approved == APPROVED_MAP["DISAPPROVED"])
        submitted = sum(1 for m in members if m.completion_status in SUBMITTED_STATUSES)

        # pendingReview: a membership counts once if it has submissions
        # but none of them has been reviewed yet.
        pending_review = 0
        for m in members:
            subs = ctx.submissions_by_membership_id.get(m.id, [])
            if subs and all(s.id not in ctx.reviews_by_submission_id for s in subs):
                pending_review += 1

        resubmittable = sum(
            1 for m in members if m.completion_status == "REJECTED_RESUBMITTABLE"
        )
        successful = sum(1 for m in members if m.completion_status == SUCCESS_STATUS)
        failed = sum(1 for m in members if m.completion_status == "FAILED")

        submission_rate = self._safe_ratio(submitted, approved)
        success_rate = self._safe_ratio(successful, submitted)

        category = ctx.categories_by_id.get(task.category_id)
        category_name = category.name if category else f"Category {task.category_id}"

        row = {
            "taskId": task.id,
            "taskName": task.name,
            "publisher": {
                "id": task.creator_id,
                "name": self._resolve_user_display_name(ctx, task.creator_id),
            },
            "category": {
                "id": task.category_id,
                "name": category_name,
            },
            "approved": APPROVED_REVERSE_MAP.get(task.approved, "NONE"),
            "createdAt": self._to_timestamp_ms(task.created_at) or 0,
            "participantCount": participant_count,
            "pendingParticipantApprovalCount": pending_approval,
            "approvedParticipantCount": approved,
            "rejectedParticipantCount": rejected,
            "submittedParticipantCount": submitted,
            "pendingReviewCount": pending_review,
            "resubmittableCount": resubmittable,
            "successfulParticipantCount": successful,
            "failedParticipantCount": failed,
            "submissionConversionRate": submission_rate,
            "successRate": success_rate,
        }
        deadline_ms = self._to_timestamp_ms(task.deadline)
        if deadline_ms is not None:
            row["deadline"] = deadline_ms
        return row

    @staticmethod
    def _resolve_user_display_name(ctx: _Context, user_id: int) -> str:
        profile = ctx.profiles_by_user_id.get(user_id)
        if profile and profile.nickname:
            return profile.nickname
        user = ctx.users_by_id.get(user_id)
        if user and user.username:
            return user.username
        return f"User {user_id}"

    # ------------------------------------------------------------------
    # Helpers: trends bucketization
    # ------------------------------------------------------------------

    @staticmethod
    def _bucket_key(dt: datetime, group_by: str) -> datetime:
        # Normalize to UTC at bucket start
        aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        if group_by == "day":
            return aware.replace(hour=0, minute=0, second=0, microsecond=0)
        if group_by == "week":
            start = aware.replace(hour=0, minute=0, second=0, microsecond=0)
            return start - timedelta(days=start.weekday())
        # month
        return aware.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def _bucketize(
        self,
        values,
        *,
        from_dt: datetime,
        to_dt: datetime,
        group_by: str,
    ) -> list[dict]:
        from_aware = from_dt if from_dt.tzinfo else from_dt.replace(tzinfo=UTC)
        to_aware = to_dt if to_dt.tzinfo else to_dt.replace(tzinfo=UTC)
        counter: Counter[datetime] = Counter()
        for v in values:
            if v is None:
                continue
            aware = v if v.tzinfo is not None else v.replace(tzinfo=UTC)
            if aware < from_aware or aware > to_aware:
                continue
            counter[self._bucket_key(aware, group_by)] += 1
        return [
            {"bucket": self._to_timestamp_ms(bucket) or 0, "count": count}
            for bucket, count in sorted(counter.items())
        ]

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

    @staticmethod
    def _safe_ratio(numerator: int, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return round(numerator / denominator, 4)

    @staticmethod
    def _to_timestamp_ms(dt: datetime | None) -> int | None:
        if dt is None:
            return None
        aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        return int(aware.timestamp() * 1000)

    @staticmethod
    def _build_distribution(name: str, counter: Counter) -> dict:
        # NT-aligned: percentage is 0..100 (not 0..1), and include the `name` field.
        total = sum(counter.values())
        items = [
            {
                "label": str(label),
                "count": count,
                "percentage": round(count / total * 100, 4) if total > 0 else 0.0,
            }
            for label, count in counter.most_common()
        ]
        return {"name": name, "type": "DISCRETE", "items": items}

    def _compute_overview_student_metrics(self, ctx: _Context) -> dict:
        """NT-aligned student metrics.

        NT computes `memberships.sumOf(studentCountOf)` where team memberships
        expand by teamMembersRealNameInfo.size. Since PY has no team-member
        real-name table yet, each membership counts as 1 student, which is
        equivalent to NT's behavior when no team snapshot data is present.
        """
        student_count = len(ctx.memberships)
        approved_student_count = sum(
            1 for m in ctx.memberships if m.approved == APPROVED_MAP["APPROVED"]
        )
        successful_student_count = sum(
            1 for m in ctx.memberships if m.completion_status == SUCCESS_STATUS
        )
        return {
            "studentCount": student_count,
            "approvedStudentCount": approved_student_count,
            "successfulStudentCount": successful_student_count,
        }

    @staticmethod
    def _csv_row(*values) -> str:
        """Render a single CSV row with RFC 4180 quoting."""
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="")
        writer.writerow(["" if v is None else v for v in values])
        return buf.getvalue()

    @staticmethod
    def _format_local_datetime_ms(ms: int | None) -> str:
        """Format an epoch-ms value as 'YYYY-MM-DD HH:MM:SS' local time.

        Empty string for None/0 so that optional deadlines render as blank.
        """
        if not ms:
            return ""
        dt = datetime.fromtimestamp(ms / 1000, tz=UTC)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _format_local_datetime(dt: datetime | None) -> str:
        if dt is None:
            return ""
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _decode_identity(identity: UserRealNameIdentity) -> dict:
        def maybe(value: str) -> str:
            if identity.encrypted and value:
                return decrypt_text(value)
            return value

        return {
            "realName": maybe(identity.real_name),
            "studentId": maybe(identity.student_id),
            "grade": maybe(identity.grade),
            "major": maybe(identity.major),
            "className": maybe(identity.class_name),
        }

    # ------------------------------------------------------------------
    # Aggregations: participants
    # ------------------------------------------------------------------

    def _filter_participant_memberships(
        self,
        memberships: list[TaskMembership],
        *,
        ctx: _Context,
        participation_approved: int | None,
        completion_status: str | None,
        real_name: str,
    ) -> list[TaskMembership]:
        result: list[TaskMembership] = []
        for m in memberships:
            if (
                participation_approved is not None
                and m.approved != participation_approved
            ):
                continue
            if (
                completion_status is not None
                and m.completion_status != completion_status
            ):
                continue
            if real_name != "all":
                has_rn = not m.is_team and m.member_id in ctx.identities_by_user_id
                if real_name == "with" and not has_rn:
                    continue
                if real_name == "without" and has_rn:
                    continue
            result.append(m)
        return result

    def _compute_participant_entity_metrics(
        self,
        memberships: list[TaskMembership],
        ctx: _Context,
    ) -> dict:
        participant_count = len(memberships)
        approved = sum(1 for m in memberships if m.approved == APPROVED_MAP["APPROVED"])
        pending = sum(1 for m in memberships if m.approved == APPROVED_MAP["NONE"])
        disapproved = sum(
            1 for m in memberships if m.approved == APPROVED_MAP["DISAPPROVED"]
        )
        submitted = sum(
            1 for m in memberships if ctx.submissions_by_membership_id.get(m.id)
        )
        successful = sum(
            1 for m in memberships if m.completion_status == SUCCESS_STATUS
        )
        return {
            "participantCount": participant_count,
            "approvedParticipantCount": approved,
            "pendingParticipantCount": pending,
            "disapprovedParticipantCount": disapproved,
            "submittedParticipantCount": submitted,
            "successfulParticipantCount": successful,
        }

    def _compute_participant_student_metrics(
        self,
        memberships: list[TaskMembership],
        ctx: _Context,
    ) -> dict:
        personal_member_ids = {m.member_id for m in memberships if not m.is_team}
        student_count = len(personal_member_ids)
        with_real_name = sum(
            1 for uid in personal_member_ids if uid in ctx.identities_by_user_id
        )
        return {
            "studentCount": student_count,
            "studentsWithRealNameCount": with_real_name,
        }

    def _compute_participant_distributions(
        self,
        memberships: list[TaskMembership],
        ctx: _Context,
    ) -> dict:
        approval_counter: Counter[str] = Counter(
            APPROVED_REVERSE_MAP.get(m.approved, "NONE") for m in memberships
        )
        completion_counter: Counter[str] = Counter(
            m.completion_status for m in memberships
        )

        grade_counter: Counter[str] = Counter()
        major_counter: Counter[str] = Counter()
        class_counter: Counter[str] = Counter()
        with_rn = 0
        without_rn = 0
        for m in memberships:
            if m.is_team:
                # PY team-member real-name linkage not yet modeled; treat as without.
                without_rn += 1
                continue
            identity = ctx.identities_by_user_id.get(m.member_id)
            if identity is None:
                without_rn += 1
                continue
            with_rn += 1
            decoded = self._decode_identity(identity)
            if decoded["grade"]:
                grade_counter[decoded["grade"]] += 1
            if decoded["major"]:
                major_counter[decoded["major"]] += 1
            if decoded["className"]:
                class_counter[decoded["className"]] += 1
        real_name_counter: Counter[str] = Counter(
            {"WITH_REAL_NAME": with_rn, "WITHOUT_REAL_NAME": without_rn}
        )

        return {
            "byApprovalStatus": self._build_distribution(
                "Participant Approval Status", approval_counter
            ),
            "byCompletionStatus": self._build_distribution(
                "Participant Completion Status", completion_counter
            ),
            "byGrade": self._build_distribution("Participant Grades", grade_counter),
            "byMajor": self._build_distribution("Participant Majors", major_counter),
            "byClassName": self._build_distribution(
                "Participant Classes", class_counter
            ),
            "byRealNameStatus": self._build_distribution(
                "Participant Real Name Status", real_name_counter
            ),
        }

    def _compute_participant_trends(
        self,
        memberships: list[TaskMembership],
        *,
        ctx: _Context,
        from_dt: datetime,
        to_dt: datetime,
        group_by: str,
    ) -> dict:
        membership_ids = {m.id for m in memberships}
        submissions = [s for s in ctx.submissions if s.membership_id in membership_ids]
        participants_joined = self._bucketize(
            (m.created_at for m in memberships),
            from_dt=from_dt,
            to_dt=to_dt,
            group_by=group_by,
        )
        submissions_created = self._bucketize(
            (s.created_at for s in submissions),
            from_dt=from_dt,
            to_dt=to_dt,
            group_by=group_by,
        )
        successes_achieved = self._bucketize(
            (
                m.updated_at
                for m in memberships
                if m.completion_status == SUCCESS_STATUS
            ),
            from_dt=from_dt,
            to_dt=to_dt,
            group_by=group_by,
        )
        return {
            "participantsJoined": participants_joined,
            "submissionsCreated": submissions_created,
            "successesAchieved": successes_achieved,
        }
