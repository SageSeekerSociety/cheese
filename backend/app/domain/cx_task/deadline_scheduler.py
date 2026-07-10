import logging
from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cx_task.models import TaskMembership

logger = logging.getLogger(__name__)

COMPLETION_STATUS_PENDING_REVIEW = "PENDING_REVIEW"
COMPLETION_STATUS_REJECTED_RESUBMITTABLE = "REJECTED_RESUBMITTABLE"
COMPLETION_STATUS_NOT_SUBMITTED = "NOT_SUBMITTED"
COMPLETION_STATUS_FAILED = "FAILED"

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
                    TaskMembership.completion_status.in_(
                        [
                            COMPLETION_STATUS_PENDING_REVIEW,
                            COMPLETION_STATUS_REJECTED_RESUBMITTABLE,
                            COMPLETION_STATUS_NOT_SUBMITTED,
                        ]
                    ),
                    TaskMembership.deleted_at.is_(None),
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

    if total_failed > 0:
        logger.info("Marked %d task memberships as FAILED due to passed deadline", total_failed)

    return total_failed
