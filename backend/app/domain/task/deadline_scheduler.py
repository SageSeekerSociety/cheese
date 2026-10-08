import logging
from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.domain.task.indexed_rows import SWEEPABLE_ROWS
from app.domain.task.models import TaskMembership
from app.domain.task.submission_state import (
    COMPLETION_STATUS_FAILED,
    has_work_in_hand,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


async def check_and_fail_expired_deadlines(session: AsyncSession) -> int:
    """Check for task memberships with passed deadlines and mark them as FAILED.

    Returns the number of memberships that were marked as failed.

    ``FAILED`` here is the same value ``submission_state`` derives for a
    membership with nothing in hand past its deadline, so this sweep never
    contradicts the axis: it only reaches the rows that would already read
    ``FAILED`` if anyone had re-derived them. Everything with work in hand —
    ``SUCCESS``, ``PENDING_REVIEW``, and a sent-back membership that handed in
    again — is out of its reach twice over (the status set and the guard).
    """
    now = datetime.now(UTC)
    total_failed = 0

    while True:
        # No offset: each commit removes matched rows from the result set,
        # so the next query at offset 0 returns the next unprocessed batch.
        stmt = (
            select(TaskMembership)
            .where(
                and_(
                    TaskMembership.deadline.isnot(None),
                    TaskMembership.deadline < now,
                    # Same text as `ix_task_membership_deadline`'s predicate, so
                    # the planner can prove the query implies the index. Written
                    # as bound parameters it could not, and the index was passed
                    # by; see `indexed_rows`.
                    SWEEPABLE_ROWS,
                    # The status alone cannot tell "never handed in" from
                    # "handed in and the review has not caught up" — only a
                    # membership with nothing in hand is out of time.
                    ~has_work_in_hand(TaskMembership.id),
                )
            )
            .limit(PAGE_SIZE)
        )

        result = await session.execute(stmt)
        memberships = list(result.scalars().all())

        if not memberships:
            break

        for membership in memberships:
            try:
                membership.completion_status = COMPLETION_STATUS_FAILED
                membership.updated_at = now
                total_failed += 1
            except Exception:
                logger.exception(
                    "Failed to update membership %s to FAILED status",
                    membership.id,
                )

        await session.commit()

        if len(memberships) < PAGE_SIZE:
            break

    return total_failed


async def sweep_expired_deadlines(sessions: SessionFactory) -> dict[str, int]:
    """The periodic entry point: one session, one sweep.

    A deadline is a promise the platform made to whoever set it, and no request
    path can keep it — the moment it matters is the moment nobody is looking.
    So a clock has to be what calls this.
    """
    async with sessions() as session:
        return {"failed": await check_and_fail_expired_deadlines(session)}
