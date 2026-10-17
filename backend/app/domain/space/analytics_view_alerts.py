"""What each number on the alerts bar counts.

One function per number, all over the context the service already loaded and
the clock the caller read once, so the six lines of the alert payload and the
six readings behind them sit together. Two of them used to be a ``sum`` written
into the report; they are the same kind of thing as the other four.

Nothing here opens a session, so a hand-built ``_Context`` is enough.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.domain.space.analytics_compute import (
    APPROVED_MAP,
    INACTIVE_PUBLISHER_DAYS,
    OVERDUE_SUBMISSION_DAYS,
    STALLED_TASK_DAYS,
    _Context,
)


def _count_pending_task_approvals(ctx: _Context) -> int:
    return sum(1 for t in ctx.tasks if t.approved == APPROVED_MAP["NONE"])


def _count_pending_participant_approvals(ctx: _Context) -> int:
    return sum(1 for m in ctx.memberships if m.approved == APPROVED_MAP["NONE"])


def _count_pending_submission_reviews(ctx: _Context) -> int:
    """A submission is pending review if it has no review row yet."""
    return sum(1 for s in ctx.submissions if s.id not in ctx.reviews_by_submission_id)


def _count_stalled_tasks(ctx: _Context, *, now: datetime) -> int:
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


def _count_overdue_unreviewed_submissions(ctx: _Context, *, now: datetime) -> int:
    """Submissions waiting for review for more than 7 days."""
    threshold = now - timedelta(days=OVERDUE_SUBMISSION_DAYS)
    return sum(
        1
        for s in ctx.submissions
        if s.id not in ctx.reviews_by_submission_id and s.created_at < threshold
    )


def _count_inactive_publishers(ctx: _Context, *, now: datetime) -> int:
    """Publishers that have created tasks historically but none in the last 30 days."""  # noqa: E501
    threshold = now - timedelta(days=INACTIVE_PUBLISHER_DAYS)
    latest_by_creator: dict[int, datetime] = {}
    for task in ctx.tasks:
        existing = latest_by_creator.get(task.creator_id)
        if existing is None or task.created_at > existing:
            latest_by_creator[task.creator_id] = task.created_at
    return sum(1 for latest in latest_by_creator.values() if latest < threshold)
