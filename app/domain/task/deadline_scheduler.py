from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.task.models import TaskMembership


logger = logging.getLogger(__name__)

MEMBERSHIP_STATUS_PENDING_REVIEW = 1
MEMBERSHIP_STATUS_REJECTED_RESUBMITTABLE = 3
MEMBERSHIP_STATUS_NOT_SUBMITTED = 4
MEMBERSHIP_STATUS_FAILED = 5

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
                    TaskMembership.member_status.in_([
                        MEMBERSHIP_STATUS_PENDING_REVIEW,
                        MEMBERSHIP_STATUS_REJECTED_RESUBMITTABLE,
                        MEMBERSHIP_STATUS_NOT_SUBMITTED,
                    ]),
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
                membership.member_status = MEMBERSHIP_STATUS_FAILED
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
