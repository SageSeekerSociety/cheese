"""Analytics computation: pure functions over an already-loaded context.

``SpaceAnalyticsViewService`` loads a space's data in bulk and hands it over as
a ``_Context``; these functions turn it into the answer payloads and the three
CSV exports. No session and no request, so a hand-built context tests them.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.core.csv_export import csv_row
from app.domain.space.models import SpaceCategory
from app.domain.task.models import (
    Task,
    TaskMembership,
    TaskSubmission,
    TaskSubmissionReview,
)
from app.domain.user.models import User, UserProfile, UserRealNameIdentity
from app.domain.user.realname_services import realname_dict

# --- Status vocabulary and alert thresholds ----------------------------------

APPROVED_MAP: dict[str, int] = {"APPROVED": 0, "DISAPPROVED": 1, "NONE": 2}
APPROVED_REVERSE_MAP: dict[int, str] = {v: k for k, v in APPROVED_MAP.items()}

SUBMITTED_STATUSES = {"PENDING_REVIEW", "REJECTED_RESUBMITTABLE", "FAILED", "SUCCESS"}
SUCCESS_STATUS = "SUCCESS"

# Alert thresholds (aligned with frontend Alerts.vue copy)
STALLED_TASK_DAYS = 14
OVERDUE_SUBMISSION_DAYS = 7
INACTIVE_PUBLISHER_DAYS = 30


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

    # When each claim started (its project was created, ``claims.started_at``);
    # a claim not started yet has no entry.
    started_at_by_membership_id: dict[int, datetime] = field(default_factory=dict)

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


# --- Helpers: conversion, timestamps, buckets --------------------------------


def safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def to_timestamp_ms(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return int(aware.timestamp() * 1000)


def build_distribution(name: str, counter: Counter) -> dict:
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


def format_local_datetime_ms(ms: int | None) -> str:
    """Format an epoch-ms value as 'YYYY-MM-DD HH:MM:SS' local time.

    Empty string for None/0 so that optional deadlines render as blank.
    """
    if not ms:
        return ""
    dt = datetime.fromtimestamp(ms / 1000, tz=UTC)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_local_datetime(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def decode_identity(identity: UserRealNameIdentity) -> dict:
    return realname_dict(identity)


def bucket_key(dt: datetime, group_by: str) -> datetime:
    # Normalize to UTC at bucket start
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    if group_by == "day":
        return aware.replace(hour=0, minute=0, second=0, microsecond=0)
    if group_by == "week":
        start = aware.replace(hour=0, minute=0, second=0, microsecond=0)
        return start - timedelta(days=start.weekday())
    # month
    return aware.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def bucketize(
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
        counter[bucket_key(aware, group_by)] += 1
    return [
        {"bucket": to_timestamp_ms(bucket) or 0, "count": count}
        for bucket, count in sorted(counter.items())
    ]


# --- CSV exports -------------------------------------------------------------


def tasks_csv_text(rows: list[dict]) -> str:
    """Tasks export: header plus one line per row, BOM included."""
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
    lines = [csv_row(*header)]
    for task in rows:
        lines.append(
            csv_row(
                task["taskId"],
                task["taskName"],
                task["category"]["name"],
                "",  # Rank column: aligned with NT (always empty in NT too)
                task["publisher"]["name"],
                format_local_datetime_ms(task["createdAt"]),
                format_local_datetime_ms(task["deadline"]),
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


def publishers_csv_text(rows: list[dict]) -> str:
    """Publishers export: header plus one line per row, BOM included."""
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
    lines = [csv_row(*header)]
    for p in rows:
        lines.append(
            csv_row(
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
                format_local_datetime_ms(p["lastTaskCreatedAt"]),
            )
        )
    # UTF-8 BOM so Excel auto-detects encoding on Windows.
    return "\ufeff" + "\n".join(lines) + "\n"


def participants_csv_text(memberships: list[TaskMembership], ctx: _Context) -> str:
    """Participants export: one line per membership, BOM included."""
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
    lines = [csv_row(*header)]
    for m in memberships:
        task = ctx.tasks_by_id.get(m.task_id)
        if task is None:
            continue
        category = ctx.categories_by_id.get(task.category_id)
        category_name = category.name if category else ""
        # NT uses creator.username directly (no nickname fallback).
        creator_user = ctx.users_by_id.get(task.creator_id)
        creator_name = creator_user.username if creator_user else ""
        identity = ctx.identities_by_user_id.get(m.member_id) if not m.is_team else None
        id_fields = (
            decode_identity(identity)
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
            csv_row(
                task.id,
                task.name,
                category_name,
                task_rank,
                creator_name,
                format_local_datetime(task.created_at),
                format_local_datetime(task.deadline),
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
                format_local_datetime(m.created_at),
            )
        )
    return "\ufeff" + "\n".join(lines) + "\n"


# --- Aggregations ------------------------------------------------------------


def compute_entity_metrics(ctx: _Context) -> dict:
    # NT-aligned: participantCount = memberships.size (no dedup).
    # submittedCount is based on whether a submission exists (not completion_status).
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

    participation_rate = safe_ratio(approved_count, len(ctx.memberships))
    submission_rate = safe_ratio(submitted_count, approved_count)
    success_rate = safe_ratio(success_count, len(ctx.memberships))

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


def compute_overview_student_metrics(ctx: _Context) -> dict:
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


def compute_task_distributions(ctx: _Context) -> dict:
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
        completion_counter[task_completion_label(members)] += 1

    return {
        "byCategory": build_distribution("Task Categories", category_counter),
        "byApprovalStatus": build_distribution(
            "Task Approval Status", approval_counter
        ),
        "byCompletionStatus": build_distribution(
            "Participant Completion Status", completion_counter
        ),
    }


def task_completion_label(members: list[TaskMembership]) -> str:
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


def compute_overview_trends(
    ctx: _Context,
    *,
    from_dt: datetime,
    to_dt: datetime,
    group_by: str,
) -> dict:
    tasks_series = bucketize(
        (t.created_at for t in ctx.tasks),
        from_dt=from_dt,
        to_dt=to_dt,
        group_by=group_by,
    )
    participants_series = bucketize(
        (m.created_at for m in ctx.memberships),
        from_dt=from_dt,
        to_dt=to_dt,
        group_by=group_by,
    )
    submissions_series = bucketize(
        (s.created_at for s in ctx.submissions),
        from_dt=from_dt,
        to_dt=to_dt,
        group_by=group_by,
    )
    successes_series = bucketize(
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


def compute_publishers_rows(ctx: _Context) -> list[dict]:
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
        # Same judgement as the overview: a member has submitted iff a
        # submission row exists (see _compute_entity_metrics).
        submitted_count = sum(
            1 for m in members if ctx.submissions_by_membership_id.get(m.id)
        )
        success_count = sum(1 for m in members if m.completion_status == SUCCESS_STATUS)

        task_count = len(tasks)
        avg_participants = (
            round(participant_count / task_count, 4) if task_count > 0 else 0.0
        )
        submission_rate = safe_ratio(submitted_count, approved_count)
        success_rate = safe_ratio(success_count, submitted_count)
        last_created_at = max(t.created_at for t in tasks)

        rows.append(
            {
                "publisherId": creator_id,
                "publisherName": resolve_user_display_name(ctx, creator_id),
                "taskCount": task_count,
                "participantCount": participant_count,
                "approvedParticipantCount": approved_count,
                "submittedParticipantCount": submitted_count,
                "successfulParticipantCount": success_count,
                "avgParticipantsPerTask": avg_participants,
                "submissionConversionRate": submission_rate,
                "successRate": success_rate,
                "lastTaskCreatedAt": to_timestamp_ms(last_created_at) or 0,
            }
        )
    return rows


def compute_task_row(task: Task, ctx: _Context) -> dict:
    members = ctx.memberships_by_task_id.get(task.id, [])
    participant_count = len(members)
    pending_approval = sum(1 for m in members if m.approved == APPROVED_MAP["NONE"])
    approved = sum(1 for m in members if m.approved == APPROVED_MAP["APPROVED"])
    rejected = sum(1 for m in members if m.approved == APPROVED_MAP["DISAPPROVED"])
    # Same judgement as the overview / publishers table: a member has
    # submitted iff a submission row exists (not completion_status, which
    # nothing advances server-side).
    submitted = sum(1 for m in members if ctx.submissions_by_membership_id.get(m.id))

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

    submission_rate = safe_ratio(submitted, approved)
    success_rate = safe_ratio(successful, submitted)

    category = ctx.categories_by_id.get(task.category_id)
    category_name = category.name if category else f"Category {task.category_id}"

    row = {
        "taskId": task.id,
        "taskName": task.name,
        "publisher": {
            "id": task.creator_id,
            "name": resolve_user_display_name(ctx, task.creator_id),
        },
        "category": {
            "id": task.category_id,
            "name": category_name,
        },
        "approved": APPROVED_REVERSE_MAP.get(task.approved, "NONE"),
        "createdAt": to_timestamp_ms(task.created_at) or 0,
        "participantCount": participant_count,
        # 没设上限的题这里是 None —— 前端据此不画「/ 上限」那一截。
        "participantLimit": task.participant_limit,
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
    deadline_ms = to_timestamp_ms(task.deadline)
    if deadline_ms is not None:
        row["deadline"] = deadline_ms
    return row


def resolve_user_display_name(ctx: _Context, user_id: int) -> str:
    profile = ctx.profiles_by_user_id.get(user_id)
    if profile and profile.nickname:
        return profile.nickname
    user = ctx.users_by_id.get(user_id)
    if user and user.username:
        return user.username
    return f"User {user_id}"
