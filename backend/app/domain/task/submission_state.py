"""Whether a 领取 handed work in, asked of the submissions rather than the status.

``TaskMembership.completion_status`` is meant to answer "how far did this person
get": nothing handed in, work sitting in the review queue, passed, or sent back.
But the only writers of that column are the claim path (``NOT_SUBMITTED``) and
the deadline sweep (``FAILED``) — ``create_review`` never advances it. So on a
membership whose work was handed in, the status still reads ``NOT_SUBMITTED``
whether the review is pending or already accepted. Any decision that trusts the
status to say "nothing was submitted" is trusting a value nobody updates.

The submission tables say the truth. A membership has work in hand when it owns
a live submission whose review is either missing (still queued) or accepted
(passed); a rejected review is a hand-back, not work in hand. That sentence has
one home — here — so the deadline sweep and whoever eventually drives the status
axis can read the same definition instead of inventing two.

That is exactly the line the status axis is supposed to draw ("accepted means
success"), which is what this guard must not contradict: it reads the reviews
the same way a writer of ``SUCCESS`` would. What it deliberately does not do is
advance the axis itself; that is a separate, larger change.
"""

from sqlalchemy import ColumnElement, exists, or_, select

from app.domain.task.models import (
    TaskMembership,
    TaskSubmission,
    TaskSubmissionReview,
)


def has_work_in_hand(membership_id: ColumnElement[int]) -> ColumnElement[bool]:
    """A correlated SQL predicate: does this membership have work in hand?

    ``membership_id`` is the membership-id column of the enclosing query (its
    ``TaskMembership.id``), which is also what the returned ``EXISTS``
    correlates against. Soft-deleted submissions and reviews are not there as
    far as their own readers in this domain are concerned — the repositories
    filter ``deleted_at IS NULL`` on both — so neither is counted here.
    """
    # The review the membership is still waiting on: no live review row at all.
    still_in_the_queue = ~exists(
        select(TaskSubmissionReview.id)
        .where(
            TaskSubmissionReview.submission_id == TaskSubmission.id,
            TaskSubmissionReview.deleted_at.is_(None),
        )
        .correlate(TaskSubmission)
    )
    # The review that says the work passed.
    passed = exists(
        select(TaskSubmissionReview.id)
        .where(
            TaskSubmissionReview.submission_id == TaskSubmission.id,
            TaskSubmissionReview.deleted_at.is_(None),
            TaskSubmissionReview.accepted.is_(True),
        )
        .correlate(TaskSubmission)
    )
    return exists(
        select(TaskSubmission.id)
        .where(
            TaskSubmission.membership_id == membership_id,
            TaskSubmission.deleted_at.is_(None),
            or_(still_in_the_queue, passed),
        )
        .correlate(TaskMembership)
    )
