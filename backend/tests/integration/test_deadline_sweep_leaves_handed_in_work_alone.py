"""A passed deadline fails the people who handed nothing in — nobody else.

The sweep used to read ``completion_status`` alone and trust it to say whether
work existed. That axis has no driver other than the claim path (which writes
``NOT_SUBMITTED``) and the sweep itself (which writes ``FAILED``):
``create_review`` never advances it. So a membership that handed work in and is
still waiting on the queue — or that was already judged accepted — still reads
``NOT_SUBMITTED``, lands in the sweepable set, and is flipped to ``FAILED`` for
having done the work. The dev database carries the scar: 15 memberships marked
``FAILED`` with a submission row under them.

This is a guard, not the root fix (that axis having no writer is tracked
separately). It asks the submission tables the question the status was trusted
to answer: is there a submission here whose review is absent (still queued) or
accepted (passed)? If so, leave the membership alone.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.domain.task.deadline_scheduler import check_and_fail_expired_deadlines
from app.domain.task.models import (
    Task,
    TaskMembership,
    TaskSubmission,
    TaskSubmissionReview,
)

pytestmark = pytest.mark.anyio

PAST = datetime.now(UTC) - timedelta(days=1)
NOT_SUBMITTED = "NOT_SUBMITTED"
REJECTED_RESUBMITTABLE = "REJECTED_RESUBMITTABLE"
FAILED = "FAILED"


async def _claim(factory, *, status: str, handed_in: str | None = None) -> int:
    """One 领取 with a deadline already behind it, plus what it handed in.

    ``handed_in``: ``None`` — nothing was ever submitted; ``"unreviewed"`` — a
    submission with no review row (the queue is the only thing left); other
    values name the verdict on its single review.
    """
    async with factory() as session:
        now = datetime.now(UTC)
        task = Task(
            name=f"sweep-{uuid.uuid4().hex[:8]}",
            intro="",
            description="",
            creator_id=1,
            space_id=1,
            category_id=1,
            submitter_type=0,
            approved=1,
            default_deadline=0,
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        await session.flush()

        membership = TaskMembership(
            task_id=task.id,
            member_id=1,
            is_team=False,
            approved=1,
            completion_status=status,
            deadline=PAST,
            created_at=now,
            updated_at=now,
        )
        session.add(membership)
        await session.flush()

        if handed_in is not None:
            submission = TaskSubmission(
                membership_id=membership.id,
                version=1,
                submitter_id=1,
                created_at=now,
                updated_at=now,
            )
            session.add(submission)
            await session.flush()
            if handed_in != "unreviewed":
                session.add(
                    TaskSubmissionReview(
                        submission_id=submission.id,
                        accepted=handed_in == "accepted",
                        score=0,
                        comment="",
                        created_at=now,
                        updated_at=now,
                    )
                )

        await session.commit()
        return int(membership.id)


async def _resubmitted_claim(factory) -> int:
    """Sent back once, handed in again — the second version is the live one."""
    async with factory() as session:
        now = datetime.now(UTC)
        task = Task(
            name=f"sweep-{uuid.uuid4().hex[:8]}",
            intro="",
            description="",
            creator_id=1,
            space_id=1,
            category_id=1,
            submitter_type=0,
            approved=1,
            default_deadline=0,
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        await session.flush()

        membership = TaskMembership(
            task_id=task.id,
            member_id=1,
            is_team=False,
            approved=1,
            completion_status=REJECTED_RESUBMITTABLE,
            deadline=PAST,
            created_at=now,
            updated_at=now,
        )
        session.add(membership)
        await session.flush()

        first = TaskSubmission(
            membership_id=membership.id,
            version=1,
            submitter_id=1,
            created_at=now,
            updated_at=now,
        )
        session.add(first)
        await session.flush()
        session.add(
            TaskSubmissionReview(
                submission_id=first.id,
                accepted=False,
                score=0,
                comment="",
                created_at=now,
                updated_at=now,
            )
        )

        second = TaskSubmission(
            membership_id=membership.id,
            version=2,
            submitter_id=1,
            created_at=now,
            updated_at=now,
        )
        session.add(second)
        await session.commit()
        return int(membership.id)


async def _sweep(factory) -> int:
    async with factory() as session:
        return await check_and_fail_expired_deadlines(session)


async def _status(factory, membership_id: int) -> str:
    async with factory() as session:
        membership = await session.scalar(
            select(TaskMembership).where(TaskMembership.id == membership_id)
        )
        assert membership is not None
        return membership.completion_status


async def test_a_submission_still_in_the_queue_is_not_failed(db_factory):
    """Handed in, waiting on a review nobody is holding against them.

    The deadline is the last moment to hand work in, and this membership did.
    Flipping it to FAILED punishes a person for a queue they do not control.
    """
    membership_id = await _claim(
        db_factory, status=NOT_SUBMITTED, handed_in="unreviewed"
    )

    failed = await _sweep(db_factory)

    assert failed == 0
    assert await _status(db_factory, membership_id) == NOT_SUBMITTED


async def test_a_submission_judged_accepted_is_not_failed(db_factory):
    """Handed in and already passed — the strongest case for leaving it be.

    Its status still reads ``NOT_SUBMITTED`` only because nothing advances that
    axis; the submission table says otherwise, and the submission table is the
    truth this sweep has to read.
    """
    membership_id = await _claim(db_factory, status=NOT_SUBMITTED, handed_in="accepted")

    failed = await _sweep(db_factory)

    assert failed == 0
    assert await _status(db_factory, membership_id) == NOT_SUBMITTED


async def test_a_member_who_never_handed_anything_in_is_failed(db_factory):
    """The sweep's whole job: an empty-handed membership past its deadline."""
    membership_id = await _claim(db_factory, status=NOT_SUBMITTED)

    failed = await _sweep(db_factory)

    assert failed == 1
    assert await _status(db_factory, membership_id) == FAILED


async def test_a_submission_judged_rejected_is_failed(db_factory):
    """Sent back and not resubmitted is the same nothing-in-hand as never
    submitting — ``REJECTED_RESUBMITTABLE`` says as much. The rejection is the
    one verdict that does not hold the membership open.
    """
    membership_id = await _claim(
        db_factory, status=REJECTED_RESUBMITTABLE, handed_in="rejected"
    )

    failed = await _sweep(db_factory)

    assert failed == 1
    assert await _status(db_factory, membership_id) == FAILED


async def test_a_sent_back_membership_that_resubmitted_is_not_failed(db_factory):
    """Sent back, resubmitted, waiting again — the second hand-in counts.

    This is the ``REJECTED_RESUBMITTABLE`` row the status alone would fail: it
    reads as "sent back", but there is work in hand again.
    """
    membership_id = await _resubmitted_claim(db_factory)

    failed = await _sweep(db_factory)

    assert failed == 0
    assert await _status(db_factory, membership_id) == REJECTED_RESUBMITTABLE
