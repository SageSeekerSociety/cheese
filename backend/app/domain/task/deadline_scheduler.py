import logging
from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.domain.task.models import TaskMembership

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

    return total_failed


async def sweep_expired_deadlines(sessions: SessionFactory) -> dict[str, int]:
    """The periodic entry point: one session, one sweep.

    A deadline is a promise the platform made to whoever set it, and no request
    path can keep it — the moment it matters is the moment nobody is looking.
    So a clock has to be what calls this.
    """
    async with sessions() as session:
        return {"failed": await check_and_fail_expired_deadlines(session)}
