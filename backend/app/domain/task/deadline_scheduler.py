import logging
from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.domain.task.models import TaskMembership
from app.domain.task.submission_state import has_work_in_hand

logger = logging.getLogger(__name__)

COMPLETION_STATUS_REJECTED_RESUBMITTABLE = "REJECTED_RESUBMITTABLE"
COMPLETION_STATUS_NOT_SUBMITTED = "NOT_SUBMITTED"
COMPLETION_STATUS_FAILED = "FAILED"

#: `PENDING_REVIEW` is deliberately NOT here. A deadline is the last moment to
#: hand work in, and someone in that state handed it in; what has not happened
#: since is the review. Failing them makes the platform punish a person for a
#: queue they do not control, and `analytics_view_service` agrees about which
#: side of the line that state is on: it counts `PENDING_REVIEW` among
#: `SUBMITTED_STATUSES`, next to `SUCCESS` and `FAILED`, not among the ongoing
#: ones. The two states below are the ones where nothing was handed in: never
#: submitted, or sent back and not resubmitted.
#:
#: Those two states are what the status *means*, not what it currently says.
#: Nothing but the claim path and this sweep writes `completion_status`, so a
#: membership that handed work in still reads `NOT_SUBMITTED` whether its review
#: is pending or already accepted — both fall in the set below and both would be
#: failed for work they did. The status is therefore necessary but not
#: sufficient: `has_work_in_hand` asks the submission tables the question the
#: status was trusted to answer, and only a membership with nothing in hand is
#: swept. See `app.domain.task.submission_state`.
_SWEEPABLE_STATUSES = [
    COMPLETION_STATUS_REJECTED_RESUBMITTABLE,
    COMPLETION_STATUS_NOT_SUBMITTED,
]

PAGE_SIZE = 100


async def check_and_fail_expired_deadlines(session: AsyncSession) -> int:
    """Check for task memberships with passed deadlines and mark them as FAILED.

    Returns the number of memberships that were marked as failed.
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
                    TaskMembership.completion_status.in_(_SWEEPABLE_STATUSES),
                    TaskMembership.deleted_at.is_(None),
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
