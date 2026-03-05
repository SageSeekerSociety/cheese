from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

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
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    total_failed = 0
    offset = 0

    while True:
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
            .offset(offset)
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

        offset += PAGE_SIZE

    if total_failed > 0:
        logger.info("Marked %d task memberships as FAILED due to passed deadline", total_failed)

    return total_failed
