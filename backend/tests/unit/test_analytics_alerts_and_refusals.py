"""Two rules the split rests on: what the alerts bar counts, and what a refusal costs.

The view service hands its reports to sibling modules; neither of these rules
may change with it.

1. **The alerts bar counts untouched work, at one clock.** The six numbers on
   ``GET /spaces/{spaceId}/analytics/alerts`` are read once, off the context the
   service already loaded: approvals nobody has decided, submissions nobody has
   reviewed, a task nobody has submitted to for 14 days, a review older than 7,
   a publisher quiet for 30. The context below puts a different number behind
   each of the six and a submission on either side of both age thresholds, so a
   count that started reading something else, or a threshold that went away,
   is a test that fails rather than a number that quietly moved.
2. **A parameter the endpoint does not take is refused before anything is
   read.** ``publishedAt`` is not a task sort field (``/tasks`` maps that name
   onto ``createdAt`` first; this layer never did), and answering it with a
   silent default is what a caller cannot tell apart from having been heard.

Both go through the service's public methods, with the reads stubbed: no
session, no database, and the clock the alerts read is the real one, so the
fixtures are built relative to now.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib import import_module
from inspect import signature
from unittest.mock import AsyncMock

import pytest

# Assemble the app up front: this file drives the app stack, and conftest now does
# that only for `client`/`python_client` (the layer ceiling counts wall clock).
import app.main  # noqa: F401
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
    TaskSubmissionReview,
)
from app.domain.user.models import User

SPACE_ID = 7
DAYS = timedelta(days=1)

NOW = datetime.now(UTC)
#: Older than the 30-day publisher line, and than both others.
QUIET = NOW - 40 * DAYS
#: Older than the 7-day review line and the 14-day stall line.
STALE = NOW - 30 * DAYS
AGED = NOW - 15 * DAYS
#: Between the two: over the review line, under the stall line.
OVERDUE_NOT_STALLED = NOW - 10 * DAYS
#: Under both lines.
FRESH = NOW - 1 * DAYS

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
        created_at=NOW,
        updated_at=NOW,
        deadline=None,
    )


def _submission(*, id: int, membership_id: int, created_at: datetime) -> TaskSubmission:
    return TaskSubmission(
        id=id,
        membership_id=membership_id,
        version=1,
        submitter_id=100,
        created_at=created_at,
        updated_at=created_at,
    )


def _context(
    *,
    tasks: list[Task] | None = None,
    memberships: list[TaskMembership] | None = None,
    submissions: list[TaskSubmission] | None = None,
    reviews_by_submission_id: dict[int, TaskSubmissionReview] | None = None,
    users_by_id: dict[int, User] | None = None,
    started: dict[int, datetime] | None = None,
) -> _Context:
    return _Context(
        tasks=tasks or [],
        memberships=memberships or [],
        submissions=submissions or [],
        reviews_by_submission_id=reviews_by_submission_id or {},
        categories_by_id={},
        users_by_id=users_by_id or {},
        profiles_by_user_id={},
        started_at_by_membership_id=started or {},
    )


def build_context() -> _Context:
    """One space holding a different number of each thing the bar counts.

    - 题 1 waits for approval, and 题 2 and 题 3 each hold two undecided
      participants: one task pending, four participants pending.
    - 题 2's approved participant submitted a day ago, so 题 2 is neither
      stalled nor its submission overdue; 题 5 is the same, with the one
      submission that has been reviewed.
    - 题 3 and 题 4 were approved and their approved participants, who
      started 40 days ago, have not submitted for 30 and 15 days: the two
      stalled tasks, and two of the five overdue submissions.
    - The remaining three overdue submissions are on memberships nobody has
      approved, and so stall nothing: two at 30 and 15 days, one at 10 —
      past the review line, short of the stall line.
    - Five publishers created the tasks; two of them within the last 30 days.
    """
    return _context(
        tasks=[
            _task(id=1, creator_id=10, approved=UNAPPROVED, created_at=QUIET),
            _task(id=2, creator_id=11, approved=APPROVED, created_at=FRESH),
            _task(id=3, creator_id=12, approved=APPROVED, created_at=QUIET),
            _task(id=4, creator_id=13, approved=APPROVED, created_at=QUIET),
            _task(id=5, creator_id=14, approved=APPROVED, created_at=FRESH),
        ],
        memberships=[
            _membership(id=1, task_id=1, member_id=100, approved=UNAPPROVED),
            _membership(id=2, task_id=2, member_id=101, approved=UNAPPROVED),
            _membership(id=3, task_id=2, member_id=102, approved=APPROVED),
            _membership(id=4, task_id=3, member_id=103, approved=UNAPPROVED),
            _membership(id=5, task_id=3, member_id=104, approved=APPROVED),
            _membership(id=6, task_id=4, member_id=105, approved=APPROVED),
            _membership(id=7, task_id=5, member_id=106, approved=APPROVED),
            _membership(id=8, task_id=5, member_id=107, approved=UNAPPROVED),
        ],
        submissions=[
            _submission(id=1, membership_id=5, created_at=STALE),
            _submission(id=2, membership_id=6, created_at=AGED),
            _submission(id=3, membership_id=3, created_at=FRESH),
            _submission(id=4, membership_id=7, created_at=FRESH),
            _submission(id=5, membership_id=1, created_at=STALE),
            _submission(id=6, membership_id=2, created_at=AGED),
            _submission(id=7, membership_id=8, created_at=OVERDUE_NOT_STALLED),
        ],
        reviews_by_submission_id={
            4: TaskSubmissionReview(
                id=1,
                submission_id=4,
                accepted=True,
                score=90,
                comment="",
                created_at=FRESH,
                updated_at=FRESH,
            )
        },
        users_by_id={
            10: User(id=10, username="p10"),
            11: User(id=11, username="p11"),
            12: User(id=12, username="p12"),
            13: User(id=13, username="p13"),
            14: User(id=14, username="p14"),
        },
        started={3: QUIET, 5: QUIET, 6: QUIET, 7: QUIET},
    )


def build_sort_context() -> _Context:
    """Three tasks from two publishers, every field the routes sort by distinct."""
    return _context(
        tasks=[
            _task(id=1, creator_id=10, approved=APPROVED, created_at=NOW - 3 * DAYS),
            _task(id=2, creator_id=11, approved=APPROVED, created_at=NOW - 2 * DAYS),
            _task(id=3, creator_id=11, approved=APPROVED, created_at=NOW - 1 * DAYS),
        ],
        users_by_id={
            10: User(id=10, username="p10"),
            11: User(id=11, username="p11"),
        },
    )


def build_service(ctx: _Context | None = None) -> SpaceAnalyticsViewService:
    service = SpaceAnalyticsViewService(AsyncMock())
    service._ensure_space_exists = AsyncMock()
    service._load_context = AsyncMock(return_value=ctx or build_context())
    return service


async def _alerts(ctx: _Context) -> dict:
    return await build_service(ctx).get_alerts(space_id=SPACE_ID)


#: The six counts ``build_context`` puts behind the bar, one bucket each. Pinned
#: rather than recomputed, and no two of them the same: the pinned payload is
#: what catches a swap, and all six differing is what keeps it able to.
EXPECTED_ALERTS = {
    "pendingTaskApprovalCount": 1,
    "pendingParticipantApprovalCount": 4,
    "pendingSubmissionReviewCount": 6,
    "stalledTaskCount": 2,
    "overdueUnreviewedSubmissionCount": 5,
    "inactivePublisherCount": 3,
}


def _assert_alerts(payload: dict) -> None:
    """The six counts, each under its own name, and no two of them equal.

    A payload that swapped two of them, or that lost the threshold behind one,
    is a report that says something else about the same space. Two buckets
    holding one count would be two buckets such a swap could hide in, so the
    pair of assertions is the point: the pinned payload tells a swap from the
    truth, and it only can while every count differs.
    """
    assert payload == EXPECTED_ALERTS
    assert len(set(payload.values())) == len(payload)


@pytest.mark.anyio
async def test_the_alerts_bar_counts_a_different_number_of_each_thing():
    """Six numbers, all different: a payload that swapped two of them, or that
    lost one, is a report that says something else about the same space."""
    _assert_alerts(await _alerts(build_context()))


@pytest.mark.anyio
async def test_swapping_two_counts_is_a_payload_the_alerts_assertion_rejects():
    """Two buckets swapped is the change the pinned payload must not survive:
    1 under 待审核题目 and 4 under 待审核报名 is the same six numbers in the wrong
    places, and a fixture whose two buckets held equal counts would let exactly
    that pass. This holds that the pair above goes red when it happens.
    """
    payload = dict(await _alerts(build_context()))
    payload["pendingTaskApprovalCount"], payload["pendingParticipantApprovalCount"] = (
        payload["pendingParticipantApprovalCount"],
        payload["pendingTaskApprovalCount"],
    )

    with pytest.raises(AssertionError):
        _assert_alerts(payload)


@pytest.mark.anyio
async def test_a_submission_leaves_the_overdue_count_once_it_is_over_a_week_old():
    """Neither review has been written; one was submitted 20 days ago and the
    other 6, so both are waiting and only the older one is overdue."""
    ctx = _context(
        submissions=[
            _submission(id=1, membership_id=1, created_at=NOW - 20 * DAYS),
            _submission(id=2, membership_id=1, created_at=NOW - 6 * DAYS),
        ],
    )

    alerts = await _alerts(ctx)

    assert alerts["pendingSubmissionReviewCount"] == 2
    assert alerts["overdueUnreviewedSubmissionCount"] == 1


@pytest.mark.anyio
async def test_a_task_is_stalled_only_once_its_approved_participants_go_quiet():
    """题 1's approved participant last submitted 20 days ago and 题 4's,
    who started 30 days ago, never has: both are stalled. 题 2's submitted two days ago, and 题 3 has nobody
    approved on it, so neither is the bar's business."""
    ctx = _context(
        tasks=[
            _task(id=1, creator_id=10, approved=APPROVED, created_at=FRESH),
            _task(id=2, creator_id=11, approved=APPROVED, created_at=FRESH),
            _task(id=3, creator_id=12, approved=APPROVED, created_at=FRESH),
            _task(id=4, creator_id=13, approved=APPROVED, created_at=FRESH),
        ],
        memberships=[
            _membership(id=1, task_id=1, member_id=100, approved=APPROVED),
            _membership(id=2, task_id=2, member_id=101, approved=APPROVED),
            _membership(id=3, task_id=3, member_id=102, approved=UNAPPROVED),
            _membership(id=4, task_id=4, member_id=103, approved=APPROVED),
        ],
        submissions=[
            _submission(id=1, membership_id=1, created_at=NOW - 20 * DAYS),
            _submission(id=2, membership_id=2, created_at=NOW - 2 * DAYS),
        ],
        started={1: STALE, 2: STALE, 4: STALE},
    )

    alerts = await _alerts(ctx)

    assert alerts["stalledTaskCount"] == 2


@pytest.mark.anyio
async def test_a_publisher_is_quiet_only_once_its_latest_task_is_a_month_old():
    """One publisher posted 40 days ago and is quiet; the other posted then
    too, and again two days ago, so it is only its latest task that counts."""
    ctx = _context(
        tasks=[
            _task(id=1, creator_id=10, approved=APPROVED, created_at=NOW - 40 * DAYS),
            _task(id=2, creator_id=11, approved=APPROVED, created_at=NOW - 40 * DAYS),
            _task(id=3, creator_id=11, approved=APPROVED, created_at=NOW - 2 * DAYS),
        ],
    )

    alerts = await _alerts(ctx)

    assert alerts["inactivePublisherCount"] == 1


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

    service._ensure_space_exists.assert_not_called()
    service._load_context.assert_not_called()


def _route_sort(module: str, endpoint: str, parameter: str) -> str:
    """The sort a route sends when the caller names none of its own.

    The parameter's default is the FastAPI ``Query`` the route declared; the
    value inside that is what the service is handed.
    """
    declared = signature(getattr(import_module(module), endpoint)).parameters[parameter]
    return declared.default.default


@pytest.mark.anyio
async def test_the_task_table_orders_by_the_sort_its_route_defaults_to():
    """The route's own default is a field this layer must take, and the rows
    come back in that order — accepting the name and sorting by nothing else
    is what a caller cannot tell apart from having been heard."""
    field = _route_sort("app.api.routes.spaces", "get_space_task_analytics", "sortBy")
    order = _route_sort(
        "app.api.routes.spaces", "get_space_task_analytics", "sortOrder"
    )

    rows = (
        await build_service(build_sort_context()).get_tasks(
            space_id=SPACE_ID,
            from_ts=None,
            to_ts=None,
            category_id=None,
            publisher_id=None,
            task_approved=None,
            has_pending_review=None,
            has_pending_approval=None,
            sort_by=field,
            sort_order=order,
        )
    )["tasks"]

    values = [row[field] for row in rows]
    assert values == sorted(values, reverse=(order == "desc"))


@pytest.mark.anyio
async def test_the_publisher_table_orders_by_the_sort_its_route_defaults_to():
    field = _route_sort(
        "app.api.routes.spaces_analytics", "get_space_analytics_publishers", "sortBy"
    )
    order = _route_sort(
        "app.api.routes.spaces_analytics", "get_space_analytics_publishers", "sortOrder"
    )

    rows = (
        await build_service(build_sort_context()).get_publishers(
            space_id=SPACE_ID,
            from_ts=None,
            to_ts=None,
            category_id=None,
            task_approved=None,
            sort_by=field,
            sort_order=order,
        )
    )["publishers"]

    values = [row[field] for row in rows]
    assert values == sorted(values, reverse=(order == "desc"))
