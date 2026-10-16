"""Two rules the split rests on: what the alerts bar counts, and what a refusal costs.

The view service hands its reports to sibling modules; neither of these rules
may change with it.

1. **The alerts bar counts untouched work, at one clock.** Each number on
   ``GET /spaces/{spaceId}/analytics/alerts`` is read once, off the context the
   service already loaded: approvals nobody has decided, submissions nobody has
   reviewed, a task nobody has submitted to for 14 days, a review older than 7
   days, a publisher quiet for 30. The context below carries exactly one of each
   kind, so a number that fell off the payload is a report that lost a count.
2. **A parameter the endpoint does not take is refused before anything is
   read.** ``publishedAt`` is not a task sort field (``/tasks`` maps that name
   onto ``createdAt`` first; this layer never did), and answering it with a
   silent default is what a caller cannot tell apart from having been heard.

Both go through the service's public methods, with the reads stubbed: no
session, no database, and the clock the alerts read is the real one, so the
fixture is built relative to now.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.core.errors import BadRequestError
from app.domain.space.analytics_compute import (
    APPROVED_MAP,
    _Context,
)
from app.domain.space.analytics_view_service import SpaceAnalyticsViewService
from app.domain.task.models import (
    Task,
    TaskMembership,
    TaskSubmission,
)
from app.domain.user.models import User

SPACE_ID = 7
DAYS = timedelta(days=1)

NOW = datetime.now(UTC)
QUIET_SINCE = NOW - 40 * DAYS
RECENT = NOW - 1 * DAYS
#: Past both thresholds the alerts read: 30 days for a publisher, 14 for a task
#: with an approved participant and no submission, 7 for an unreviewed one.
STALE = NOW - 30 * DAYS

UNAPPROVED = APPROVED_MAP["NONE"]
APPROVED = APPROVED_MAP["APPROVED"]


def _task(*, id: int, creator_id: int, approved: int, created_at: datetime) -> Task:
    return Task(
        id=id,
        name=f"题 {id}",
        intro="",
        description="",
        creator_id=creator_id,
        space_id=SPACE_ID,
        category_id=1,
        submitter_type=0,
        approved=approved,
        participant_limit=None,
        deadline=None,
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
    *, id: int, task_id: int, member_id: int, approved: int
) -> TaskMembership:
    return TaskMembership(
        id=id,
        task_id=task_id,
        member_id=member_id,
        approved=approved,
        is_team=False,
        email=f"m{member_id}@example.com",
        phone="",
        completion_status="NOT_SUBMITTED",
        created_at=STALE,
        updated_at=STALE,
        deadline=None,
    )


def _submission(*, id: int, membership_id: int) -> TaskSubmission:
    return TaskSubmission(
        id=id,
        membership_id=membership_id,
        version=1,
        submitter_id=100,
        created_at=STALE,
        updated_at=STALE,
    )


def build_context() -> _Context:
    """One space holding exactly one of each thing the alerts bar counts.

    - 题 1 waits for approval and its publisher has been quiet for 40 days;
    - 题 2's publisher is active, and it holds the one undecided participant;
    - 题 3's participant was approved 30 days ago and nothing has been submitted
      to it since — the same submission nobody has reviewed.
    """
    return _Context(
        tasks=[
            _task(id=1, creator_id=10, approved=UNAPPROVED, created_at=QUIET_SINCE),
            _task(id=2, creator_id=11, approved=APPROVED, created_at=RECENT),
            _task(id=3, creator_id=12, approved=APPROVED, created_at=RECENT),
        ],
        memberships=[
            _membership(id=1, task_id=1, member_id=100, approved=APPROVED),
            _membership(id=2, task_id=2, member_id=101, approved=UNAPPROVED),
            _membership(id=3, task_id=3, member_id=102, approved=APPROVED),
        ],
        submissions=[
            _submission(id=1, membership_id=1),
            _submission(id=2, membership_id=3),
        ],
        reviews_by_submission_id={},
        categories_by_id={},
        users_by_id={10: User(id=10, username="p10")},
        profiles_by_user_id={},
    )


def build_service() -> SpaceAnalyticsViewService:
    service = SpaceAnalyticsViewService(AsyncMock())
    service._ensure_space_exists = AsyncMock()
    service._load_context = AsyncMock(return_value=build_context())
    return service


@pytest.mark.anyio
async def test_the_alerts_bar_counts_the_one_of_each_the_space_holds():
    alerts = await build_service().get_alerts(space_id=SPACE_ID)

    assert alerts == {
        "pendingTaskApprovalCount": 1,
        "pendingParticipantApprovalCount": 1,
        "pendingSubmissionReviewCount": 2,
        "stalledTaskCount": 2,
        "overdueUnreviewedSubmissionCount": 2,
        "inactivePublisherCount": 1,
    }


@pytest.mark.anyio
async def test_each_alert_number_is_the_class_helper_a_test_can_stub():
    """Every number is read off the service, so each one has a patch point.

    Two of them used to be a ``sum`` written into the payload; a stub on the
    service that the payload ignores is a number nothing can drive.
    """
    service = build_service()
    service._count_pending_task_approvals = lambda ctx: 11
    service._count_stalled_tasks = lambda ctx, *, now: 22

    alerts = await service.get_alerts(space_id=SPACE_ID)

    assert alerts["pendingTaskApprovalCount"] == 11
    assert alerts["stalledTaskCount"] == 22
    assert alerts["pendingParticipantApprovalCount"] == 1


@pytest.mark.anyio
async def test_a_sort_field_this_endpoint_does_not_take_is_refused_before_the_read():
    service = build_service()

    with pytest.raises(BadRequestError):
        await service.get_tasks(
            space_id=SPACE_ID,
            from_ts=None,
            to_ts=None,
            category_id=None,
            publisher_id=None,
            task_approved=None,
            has_pending_review=None,
            has_pending_approval=None,
            sort_by="publishedAt",
            sort_order="desc",
        )

    service._load_context.assert_not_called()


@pytest.mark.anyio
async def test_the_documented_sorts_are_the_ones_it_takes():
    """The default the route documents must not 400 the caller that takes it."""
    service = build_service()

    await service.get_tasks(
        space_id=SPACE_ID,
        from_ts=None,
        to_ts=None,
        category_id=None,
        publisher_id=None,
        task_approved=None,
        has_pending_review=None,
        has_pending_approval=None,
        sort_by="createdAt",
        sort_order="desc",
    )
    await service.get_publishers(
        space_id=SPACE_ID,
        from_ts=None,
        to_ts=None,
        category_id=None,
        task_approved=None,
        sort_by="taskCount",
        sort_order="desc",
    )
