"""The analytics reports answer the same numbers, whatever module computes them.

The pure computation of ``analytics_view_service`` moved to the sibling modules
``analytics_compute`` and ``analytics_participant_compute``. Nothing a caller
sees may change with it: how many participants a task counts, what the three
CSV exports contain byte for byte, and which buckets a trend window produces.

Every expectation below was captured from the implementation before that move,
so a difference here is the move changing a report. The service is driven
through its public methods with ``_load_context`` stubbed out: what is pinned
is the report it builds from a context, not the SQL that fills the context.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.domain.space.analytics_view_service import (
    SpaceAnalyticsViewService,
    _Context,
)
from app.domain.space.models import SpaceCategory
from app.domain.task.models import (
    Task,
    TaskMembership,
    TaskSubmission,
    TaskSubmissionReview,
)
from app.domain.user.models import User, UserProfile, UserRealNameIdentity

SPACE_ID = 7
WINDOW_FROM_DT = datetime(2025, 1, 1, tzinfo=UTC)
WINDOW_TO_DT = datetime(2025, 1, 4, tzinfo=UTC)
WINDOW_FROM = int(WINDOW_FROM_DT.timestamp() * 1000)
WINDOW_TO = int(WINDOW_TO_DT.timestamp() * 1000)

D1 = datetime(2025, 1, 1, 9, 0, 0)
D2 = datetime(2025, 1, 2, 9, 0, 0)
D3 = datetime(2025, 1, 3, 9, 0, 0)
# Exactly the window's last instant, and just past it: one is inside the
# window and the other is not, so the closed upper bound is pinned.
BOUNDARY = datetime(2025, 1, 4, 0, 0, 0)
AFTER_WINDOW = datetime(2025, 1, 4, 9, 0, 0)


def _task(
    *,
    id: int,
    creator_id: int,
    category_id: int,
    approved: int,
    name: str,
    created_at: datetime,
    deadline: datetime | None,
    participant_limit: int | None = None,
) -> Task:
    return Task(
        id=id,
        name=name,
        intro="简介",
        description="",
        creator_id=creator_id,
        space_id=SPACE_ID,
        category_id=category_id,
        submitter_type=0,
        approved=approved,
        participant_limit=participant_limit,
        deadline=deadline,
        default_deadline=0,
        resubmittable=False,
        editable=True,
        require_real_name=True,
        team_locking_policy="NONE",
        access_control_enabled=False,
        created_at=created_at,
        updated_at=created_at,
    )


def _membership(
    *,
    id: int,
    task_id: int,
    member_id: int,
    approved: int,
    completion_status: str,
    created_at: datetime,
    is_team: bool = False,
) -> TaskMembership:
    return TaskMembership(
        id=id,
        task_id=task_id,
        member_id=member_id,
        approved=approved,
        is_team=is_team,
        email=f"m{member_id}@example.com",
        phone=f"1380000{member_id:04d}",
        completion_status=completion_status,
        created_at=created_at,
        updated_at=created_at,
        deadline=None,
    )


def _identity(id: int, user_id: int, real_name: str) -> UserRealNameIdentity:
    return UserRealNameIdentity(
        id=id,
        user_id=user_id,
        encrypted=False,
        real_name=real_name,
        student_id=f"2023{user_id:04d}",
        grade="高一",
        major="计算机",
        class_name="3班",
        created_at=D1,
        updated_at=D1,
    )


def build_context() -> _Context:
    """One space with two tasks, three memberships and one reviewed submission."""
    tasks = [
        _task(
            id=1,
            creator_id=10,
            category_id=1,
            approved=0,
            name="秋季赛",
            created_at=D1,
            deadline=datetime(2025, 1, 2, 0, 0, 0),
            participant_limit=5,
        ),
        _task(
            id=2,
            creator_id=11,
            category_id=2,
            approved=0,
            name="春季赛",
            created_at=D2,
            deadline=datetime(2025, 1, 3, 0, 0, 0),
        ),
        # Created the day after the window closes: the trends are over the
        # window, so this one is counted nowhere in them.
        _task(
            id=3,
            creator_id=11,
            category_id=2,
            approved=0,
            name="夏季赛",
            created_at=AFTER_WINDOW,
            deadline=datetime(2025, 1, 5, 0, 0, 0),
        ),
    ]
    memberships = [
        _membership(
            id=1,
            task_id=1,
            member_id=100,
            approved=0,
            completion_status="SUCCESS",
            created_at=D1,
            is_team=True,
        ),
        _membership(
            id=2,
            task_id=1,
            member_id=101,
            approved=0,
            completion_status="PENDING_REVIEW",
            created_at=D2,
        ),
        _membership(
            id=3,
            task_id=2,
            member_id=102,
            approved=2,
            completion_status="NOT_SUBMITTED",
            created_at=D3,
        ),
        # The same member as m1, on the other task: four membership rows over
        # three people, so a report that deduplicated would be caught.
        _membership(
            id=4,
            task_id=2,
            member_id=100,
            approved=0,
            completion_status="SUCCESS",
            created_at=D3,
        ),
        # Joined exactly on the window's last instant, so it is kept.
        _membership(
            id=5,
            task_id=1,
            member_id=103,
            approved=0,
            completion_status="SUCCESS",
            created_at=BOUNDARY,
        ),
        # Member 102 again, on the other task of the same publisher: the
        # publisher's row counts that person once.
        _membership(
            id=6,
            task_id=3,
            member_id=102,
            approved=2,
            completion_status="NOT_SUBMITTED",
            created_at=D3,
        ),
    ]
    submissions = [
        TaskSubmission(
            id=1,
            membership_id=2,
            version=1,
            submitter_id=101,
            created_at=D2,
            updated_at=D2,
        )
    ]
    reviews = {
        1: TaskSubmissionReview(
            id=1,
            submission_id=1,
            accepted=True,
            score=88,
            comment="不错",
            created_at=D3,
            updated_at=D3,
        )
    }
    return _Context(
        tasks=tasks,
        memberships=memberships,
        submissions=submissions,
        reviews_by_submission_id=reviews,
        categories_by_id={
            1: SpaceCategory(id=1, space_id=SPACE_ID, name="高一"),
            2: SpaceCategory(id=2, space_id=SPACE_ID, name="高二"),
        },
        users_by_id={
            10: User(id=10, username="publisher_a"),
            11: User(id=11, username="publisher_b"),
            100: User(id=100, username="student_1"),
            101: User(id=101, username="student_2"),
            102: User(id=102, username="student_3"),
            103: User(id=103, username="student_4"),
        },
        profiles_by_user_id={
            10: UserProfile(user_id=10, nickname="张老师"),
            11: UserProfile(user_id=11, nickname=""),
        },
        identities_by_user_id={
            100: _identity(1, 100, "李四"),
            102: _identity(2, 102, "王五"),
        },
        member_users_by_user_id={},
    )


def build_service() -> SpaceAnalyticsViewService:
    service = SpaceAnalyticsViewService(AsyncMock())
    service._ensure_space_exists = AsyncMock()
    service._load_context = AsyncMock(return_value=build_context())
    return service


async def _overview() -> dict:
    return await build_service().get_overview(
        space_id=SPACE_ID,
        from_ts=WINDOW_FROM,
        to_ts=WINDOW_TO,
        category_id=None,
        publisher_id=None,
        task_approved=None,
        group_by="day",
    )


async def _publishers() -> dict:
    return await build_service().get_publishers(
        space_id=SPACE_ID,
        from_ts=WINDOW_FROM,
        to_ts=WINDOW_TO,
        category_id=None,
        task_approved=None,
        sort_by="taskCount",
        sort_order="desc",
    )


async def _tasks() -> dict:
    return await build_service().get_tasks(
        space_id=SPACE_ID,
        from_ts=WINDOW_FROM,
        to_ts=WINDOW_TO,
        category_id=None,
        publisher_id=None,
        task_approved=None,
        has_pending_review=None,
        has_pending_approval=None,
        sort_by="createdAt",
        sort_order="asc",
    )


async def _participants() -> dict:
    return await build_service().get_participants(
        space_id=SPACE_ID,
        from_ts=WINDOW_FROM,
        to_ts=WINDOW_TO,
        category_id=None,
        publisher_id=None,
        task_approved=None,
        participation_approved=None,
        completion_status=None,
        real_name="all",
        group_by="day",
    )


async def _tasks_csv() -> str:
    return await build_service().export_tasks_csv(
        space_id=SPACE_ID,
        from_ts=WINDOW_FROM,
        to_ts=WINDOW_TO,
        category_id=None,
        publisher_id=None,
        task_approved=None,
        has_pending_review=None,
        has_pending_approval=None,
    )


async def _publishers_csv() -> str:
    return await build_service().export_publishers_csv(
        space_id=SPACE_ID,
        from_ts=WINDOW_FROM,
        to_ts=WINDOW_TO,
        category_id=None,
        task_approved=None,
    )


async def _participants_csv() -> str:
    text, _ = await build_service().export_participants_csv(
        space_id=SPACE_ID,
        from_ts=WINDOW_FROM,
        to_ts=WINDOW_TO,
        category_id=None,
        publisher_id=None,
        task_approved=None,
        participation_approved=None,
        completion_status=None,
        real_name="all",
    )
    return text


# ---------------------------------------------------------------------------
# The rules those numbers stand for, stated so they can be judged on their own
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_every_membership_counts_as_a_participant_without_deduplication():
    """Two members join two tasks each, so the six memberships are only four
    people: the space counts rows, and reports six."""
    report = await _overview()
    assert report["entityMetrics"]["participantCount"] == 6
    assert report["entityMetrics"]["approvedParticipantCount"] == 4
    assert report["studentMetrics"]["studentCount"] == 6


@pytest.mark.anyio
async def test_a_membership_without_a_submission_is_not_submitted():
    """A member is submitted only once a submission row exists for them, so the
    one reviewed submission below counts for its membership alone, while the
    three memberships that carry a SUCCESS status on their own do not."""
    metrics = (await _overview())["entityMetrics"]
    assert metrics["submittedParticipantCount"] == 1
    assert metrics["successfulParticipantCount"] == 3


@pytest.mark.anyio
async def test_the_success_rate_is_over_every_membership():
    """Three successes out of six memberships, not out of the one submitted."""
    assert (await _overview())["entityMetrics"]["successRate"] == pytest.approx(
        3 / 6, abs=1e-4
    )


@pytest.mark.anyio
async def test_a_publisher_counts_a_person_once_across_its_tasks():
    """Where the space counts rows, a publisher counts people: its two tasks
    have three memberships between them, and one member is on both."""
    rows = (await _publishers())["publishers"]
    by_id = {row["publisherId"]: row for row in rows}
    assert by_id[11]["taskCount"] == 2
    assert by_id[11]["participantCount"] == 2
    assert by_id[10]["participantCount"] == 3


@pytest.mark.anyio
async def test_a_trend_window_keeps_its_last_instant_and_drops_the_rest():
    """A join at exactly ``to`` belongs to the window; the task created after
    it does not, so ``tasksCreated`` is the two tasks inside the range."""
    trends = (await _overview())["trends"]
    assert sum(point["count"] for point in trends["tasksCreated"]) == 2
    assert sum(point["count"] for point in trends["participantsJoined"]) == 6
    assert trends["participantsJoined"][-1] == {
        "bucket": WINDOW_TO,
        "count": 1,
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("group_by", "expected"),
    [
        # 2025-01-01 is a Wednesday, so the whole window sits in the week
        # starting Monday 2024-12-30.
        ("week", [(1735516800000, 6)]),
        # ... and in January 2025, which starts on 2025-01-01.
        ("month", [(1735689600000, 6)]),
    ],
)
async def test_a_coarser_grouping_keeps_every_value_in_its_own_bucket(
    group_by, expected
):
    """Day, week and month are three views of the same six joins: a coarser
    grouping may not lose or duplicate one, and its bucket starts where its
    own calendar says."""
    service = build_service()
    coarse = await service.get_overview(
        space_id=SPACE_ID,
        from_ts=WINDOW_FROM,
        to_ts=WINDOW_TO,
        category_id=None,
        publisher_id=None,
        task_approved=None,
        group_by=group_by,
    )
    points = coarse["trends"]["participantsJoined"]
    assert [(point["bucket"], point["count"]) for point in points] == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("export", "columns"),
    [
        (
            _tasks_csv,
            [
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
            ],
        ),
        (
            _publishers_csv,
            [
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
            ],
        ),
        (
            _participants_csv,
            [
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
            ],
        ),
    ],
)
async def test_each_export_keeps_its_columns_in_order(export, columns):
    """Downstream parsers index these files by position; the split may not
    move, rename or drop a column."""
    text = await export()
    assert next(csv.reader(io.StringIO(text.lstrip("\ufeff")))) == columns


# ---------------------------------------------------------------------------
# What the reports answered before the computation moved


EXPECTED_OVERVIEW = {
    "summary": {"spaceId": 7, "from": 1735689600000, "to": 1735948800000},
    "entityMetrics": {
        "taskCount": 3,
        "publisherCount": 2,
        "participantCount": 6,
        "approvedParticipantCount": 4,
        "submittedParticipantCount": 1,
        "successfulParticipantCount": 3,
        "participationConversionRate": 0.6667,
        "submissionConversionRate": 0.25,
        "successRate": 0.5,
    },
    "studentMetrics": {
        "studentCount": 6,
        "approvedStudentCount": 4,
        "successfulStudentCount": 3,
    },
    "taskDistributions": {
        "byCategory": {
            "name": "Task Categories",
            "type": "DISCRETE",
            "items": [
                {"label": "高二", "count": 2, "percentage": 66.6667},
                {"label": "高一", "count": 1, "percentage": 33.3333},
            ],
        },
        "byApprovalStatus": {
            "name": "Task Approval Status",
            "type": "DISCRETE",
            "items": [{"label": "APPROVED", "count": 3, "percentage": 100.0}],
        },
        "byCompletionStatus": {
            "name": "Participant Completion Status",
            "type": "DISCRETE",
            "items": [
                {"label": "PARTIAL_SUCCESS", "count": 2, "percentage": 66.6667},
                {"label": "NOT_STARTED", "count": 1, "percentage": 33.3333},
            ],
        },
    },
    "trends": {
        "tasksCreated": [
            {"bucket": 1735689600000, "count": 1},
            {"bucket": 1735776000000, "count": 1},
        ],
        "participantsJoined": [
            {"bucket": 1735689600000, "count": 1},
            {"bucket": 1735776000000, "count": 1},
            {"bucket": 1735862400000, "count": 3},
            {"bucket": 1735948800000, "count": 1},
        ],
        "submissionsCreated": [{"bucket": 1735776000000, "count": 1}],
        "successesAchieved": [
            {"bucket": 1735689600000, "count": 1},
            {"bucket": 1735862400000, "count": 1},
            {"bucket": 1735948800000, "count": 1},
        ],
    },
}

EXPECTED_PUBLISHERS = {
    "publishers": [
        {
            "publisherId": 11,
            "publisherName": "publisher_b",
            "taskCount": 2,
            "participantCount": 2,
            "approvedParticipantCount": 1,
            "submittedParticipantCount": 0,
            "successfulParticipantCount": 1,
            "avgParticipantsPerTask": 1.0,
            "submissionConversionRate": 0.0,
            "successRate": 0.0,
            "lastTaskCreatedAt": 1735981200000,
        },
        {
            "publisherId": 10,
            "publisherName": "张老师",
            "taskCount": 1,
            "participantCount": 3,
            "approvedParticipantCount": 3,
            "submittedParticipantCount": 1,
            "successfulParticipantCount": 2,
            "avgParticipantsPerTask": 3.0,
            "submissionConversionRate": 0.3333,
            "successRate": 2.0,
            "lastTaskCreatedAt": 1735722000000,
        },
    ]
}

EXPECTED_TASKS = {
    "tasks": [
        {
            "taskId": 1,
            "taskName": "秋季赛",
            "publisher": {"id": 10, "name": "张老师"},
            "category": {"id": 1, "name": "高一"},
            "approved": "APPROVED",
            "createdAt": 1735722000000,
            "participantCount": 3,
            "participantLimit": 5,
            "pendingParticipantApprovalCount": 0,
            "approvedParticipantCount": 3,
            "rejectedParticipantCount": 0,
            "submittedParticipantCount": 1,
            "pendingReviewCount": 0,
            "resubmittableCount": 0,
            "successfulParticipantCount": 2,
            "failedParticipantCount": 0,
            "submissionConversionRate": 0.3333,
            "successRate": 2.0,
            "deadline": 1735776000000,
        },
        {
            "taskId": 2,
            "taskName": "春季赛",
            "publisher": {"id": 11, "name": "publisher_b"},
            "category": {"id": 2, "name": "高二"},
            "approved": "APPROVED",
            "createdAt": 1735808400000,
            "participantCount": 2,
            "participantLimit": None,
            "pendingParticipantApprovalCount": 1,
            "approvedParticipantCount": 1,
            "rejectedParticipantCount": 0,
            "submittedParticipantCount": 0,
            "pendingReviewCount": 0,
            "resubmittableCount": 0,
            "successfulParticipantCount": 1,
            "failedParticipantCount": 0,
            "submissionConversionRate": 0.0,
            "successRate": 0.0,
            "deadline": 1735862400000,
        },
        {
            "taskId": 3,
            "taskName": "夏季赛",
            "publisher": {"id": 11, "name": "publisher_b"},
            "category": {"id": 2, "name": "高二"},
            "approved": "APPROVED",
            "createdAt": 1735981200000,
            "participantCount": 1,
            "participantLimit": None,
            "pendingParticipantApprovalCount": 1,
            "approvedParticipantCount": 0,
            "rejectedParticipantCount": 0,
            "submittedParticipantCount": 0,
            "pendingReviewCount": 0,
            "resubmittableCount": 0,
            "successfulParticipantCount": 0,
            "failedParticipantCount": 0,
            "submissionConversionRate": 0.0,
            "successRate": 0.0,
            "deadline": 1736035200000,
        },
    ]
}

EXPECTED_PARTICIPANTS = {
    "summary": {"spaceId": 7, "from": 1735689600000, "to": 1735948800000},
    "entityMetrics": {
        "participantCount": 6,
        "approvedParticipantCount": 4,
        "pendingParticipantCount": 2,
        "disapprovedParticipantCount": 0,
        "submittedParticipantCount": 1,
        "successfulParticipantCount": 3,
    },
    "studentMetrics": {"studentCount": 4, "studentsWithRealNameCount": 2},
    "distributions": {
        "byApprovalStatus": {
            "name": "Participant Approval Status",
            "type": "DISCRETE",
            "items": [
                {"label": "APPROVED", "count": 4, "percentage": 66.6667},
                {"label": "NONE", "count": 2, "percentage": 33.3333},
            ],
        },
        "byCompletionStatus": {
            "name": "Participant Completion Status",
            "type": "DISCRETE",
            "items": [
                {"label": "SUCCESS", "count": 3, "percentage": 50.0},
                {"label": "NOT_SUBMITTED", "count": 2, "percentage": 33.3333},
                {"label": "PENDING_REVIEW", "count": 1, "percentage": 16.6667},
            ],
        },
        "byGrade": {
            "name": "Participant Grades",
            "type": "DISCRETE",
            "items": [{"label": "高一", "count": 3, "percentage": 100.0}],
        },
        "byMajor": {
            "name": "Participant Majors",
            "type": "DISCRETE",
            "items": [{"label": "计算机", "count": 3, "percentage": 100.0}],
        },
        "byClassName": {
            "name": "Participant Classes",
            "type": "DISCRETE",
            "items": [{"label": "3班", "count": 3, "percentage": 100.0}],
        },
        "byRealNameStatus": {
            "name": "Participant Real Name Status",
            "type": "DISCRETE",
            "items": [
                {"label": "WITH_REAL_NAME", "count": 3, "percentage": 50.0},
                {"label": "WITHOUT_REAL_NAME", "count": 3, "percentage": 50.0},
            ],
        },
    },
    "trends": {
        "participantsJoined": [
            {"bucket": 1735689600000, "count": 1},
            {"bucket": 1735776000000, "count": 1},
            {"bucket": 1735862400000, "count": 3},
            {"bucket": 1735948800000, "count": 1},
        ],
        "submissionsCreated": [{"bucket": 1735776000000, "count": 1}],
        "successesAchieved": [
            {"bucket": 1735689600000, "count": 1},
            {"bucket": 1735862400000, "count": 1},
            {"bucket": 1735948800000, "count": 1},
        ],
    },
}


def _csv(*rows: str) -> str:
    """An export as the file itself: a BOM, then one line per row."""
    return "\ufeff" + "".join(row + "\n" for row in rows)


EXPECTED_TASKS_CSV = _csv(
    "Task ID,Task Title,Category,Rank,Creator,Created At,Deadline,"
    "Total Participants,Approved,Rejected,Pending,Submitted,Pending Review,"
    "Completed,Failed,Task Status",
    "3,夏季赛,高二,,publisher_b,2025-01-04 09:00:00,2025-01-05 00:00:00,1,0,0,1,"
    "0,0,0,0,APPROVED",
    "2,春季赛,高二,,publisher_b,2025-01-02 09:00:00,2025-01-03 00:00:00,2,1,0,1,"
    "0,0,1,0,APPROVED",
    "1,秋季赛,高一,,张老师,2025-01-01 09:00:00,2025-01-02 00:00:00,3,3,0,0,1,0,2,0,"
    "APPROVED",
)

EXPECTED_PUBLISHERS_CSV = _csv(
    "Publisher ID,Publisher Name,Total Tasks,Total Participants,"
    "Approved Participants,Submitted Participants,Successful Participants,"
    "Average Participants Per Task,Submission Conversion Rate,Success Rate,"
    "Last Task Created At",
    "11,publisher_b,2,2,1,0,1,1.0,0.0,0.0,2025-01-04 09:00:00",
    "10,张老师,1,3,3,1,2,3.0,0.3333,2.0,2025-01-01 09:00:00",
)

EXPECTED_PARTICIPANTS_CSV = _csv(
    "Task ID,Task Title,Category,Task Rank,Task Creator,Created At,"
    "Deadline,Member ID,Username,Real Name,Student ID,Grade,Major,Class,"
    "Phone,Email,Apply Reason,Reject Reason,Approval Status,"
    "Completion Status,Is Team,Join Date",
    "1,秋季赛,高一,,publisher_a,2025-01-01 09:00:00,2025-01-02 00:00:00,100,,,,,"
    ",,13800000100,m100@example.com,,,APPROVED,SUCCESS,true,"
    "2025-01-01 09:00:00",
    "1,秋季赛,高一,,publisher_a,2025-01-01 09:00:00,2025-01-02 00:00:00,101,,,,,"
    ",,13800000101,m101@example.com,,,APPROVED,PENDING_REVIEW,false,"
    "2025-01-02 09:00:00",
    "2,春季赛,高二,,publisher_b,2025-01-02 09:00:00,2025-01-03 00:00:00,102,,王五,"
    "20230102,高一,计算机,3班,13800000102,m102@example.com,,,NONE,NOT_SUBMITTED,"
    "false,2025-01-03 09:00:00",
    "2,春季赛,高二,,publisher_b,2025-01-02 09:00:00,2025-01-03 00:00:00,100,,李四,"
    "20230100,高一,计算机,3班,13800000100,m100@example.com,,,APPROVED,SUCCESS,"
    "false,2025-01-03 09:00:00",
    "1,秋季赛,高一,,publisher_a,2025-01-01 09:00:00,2025-01-02 00:00:00,103,,,,,"
    ",,13800000103,m103@example.com,,,APPROVED,SUCCESS,false,"
    "2025-01-04 00:00:00",
    "3,夏季赛,高二,,publisher_b,2025-01-04 09:00:00,2025-01-05 00:00:00,102,,王五,"
    "20230102,高一,计算机,3班,13800000102,m102@example.com,,,NONE,NOT_SUBMITTED,"
    "false,2025-01-03 09:00:00",
)


@pytest.mark.anyio
async def test_the_overview_report_counts_what_it_counted_before():
    assert await _overview() == EXPECTED_OVERVIEW


@pytest.mark.anyio
async def test_the_publisher_report_counts_what_it_counted_before():
    assert await _publishers() == EXPECTED_PUBLISHERS


@pytest.mark.anyio
async def test_the_task_report_counts_what_it_counted_before():
    assert await _tasks() == EXPECTED_TASKS


@pytest.mark.anyio
async def test_the_participant_report_counts_what_it_counted_before():
    assert await _participants() == EXPECTED_PARTICIPANTS


@pytest.mark.anyio
async def test_the_task_export_writes_the_same_csv():
    assert await _tasks_csv() == EXPECTED_TASKS_CSV


@pytest.mark.anyio
async def test_the_publisher_export_writes_the_same_csv():
    assert await _publishers_csv() == EXPECTED_PUBLISHERS_CSV


@pytest.mark.anyio
async def test_the_participant_export_writes_the_same_csv():
    assert await _participants_csv() == EXPECTED_PARTICIPANTS_CSV
