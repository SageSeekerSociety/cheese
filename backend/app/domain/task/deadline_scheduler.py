import logging
from datetime import UTC, datetime

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.domain.task.indexed_rows import SWEEPABLE_ROWS
from app.domain.task.models import TaskMembership, TaskStage
from app.domain.task.submission_state import (
    COMPLETION_STATUS_FAILED,
    derive_completion_status,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


async def check_and_fail_expired_deadlines(session: AsyncSession) -> int:
    """Mark as FAILED the memberships that have run out of time with nothing in
    hand: past their own deadline, or past a stage's due date with that stage
    neither handed in nor passed.

    Returns the number of memberships that were marked as failed.

    Each candidate is derived with ``submission_state.derive_completion_status``
    and written only when that says ``FAILED``, so this sweep never contradicts
    the axis: it reaches only the rows that would read ``FAILED`` if anyone had
    re-derived them. Everything with work in hand — a stage waiting on review,
    every stage passed — derives to something else and is left alone.
    """
    now = datetime.now(UTC)
    # Past the claim's own deadline. Same text as `ix_task_membership_deadline`'s
    # predicate, so the planner can prove the query implies the index; written
    # as bound parameters it could not, and the index was passed by (see
    # `indexed_rows`).
    past_own = [
        TaskMembership.deadline.isnot(None),
        TaskMembership.deadline < now,
        SWEEPABLE_ROWS,
    ]
    # An approved claim on a task with a stage already due. A pending or
    # rejected application hands nothing in, so no stage can run out on it.
    past_stage = [
        SWEEPABLE_ROWS,
        TaskMembership.approved == 0,
        TaskMembership.task_id.in_(
            select(TaskStage.task_id).where(
                TaskStage.due_at.isnot(None), TaskStage.due_at < now
            )
        ),
    ]
    failed: set[int] = set()
    # The first pass walks the index in its own order, so the planner reads it
    # rather than the primary key.
    failed |= await _sweep(
        session,
        past_own,
        (TaskMembership.deadline, TaskMembership.id),
        (datetime.min.replace(tzinfo=UTC), 0),
        now,
    )
    failed |= await _sweep(session, past_stage, (TaskMembership.id,), (0,), now)
    return len(failed)


async def _sweep(
    session: AsyncSession, where: list, keys: tuple, after: tuple, now: datetime
) -> set[int]:
    failed: set[int] = set()
    # Keyset paging on ``keys``, from below the first row: a candidate that
    # derives to something other than FAILED stays in the result set, so
    # paging from the start again would loop on it.
    while True:
        memberships = list(
            (
                await session.execute(
                    select(TaskMembership)
                    .where(*where, tuple_(*keys) > tuple_(*after))
                    .order_by(*keys)
                    .limit(PAGE_SIZE)
                )
            )
            .scalars()
            .all()
        )
        if not memberships:
            break
        after = tuple(getattr(memberships[-1], key.key) for key in keys)
        for membership in memberships:
            try:
                status = await derive_completion_status(session, membership, now=now)
            except Exception:
                logger.exception(
                    "Failed to derive membership %s for the deadline sweep",
                    membership.id,
                )
                continue
            if status == COMPLETION_STATUS_FAILED:
                membership.completion_status = COMPLETION_STATUS_FAILED
                membership.updated_at = now
                failed.add(int(membership.id))
        await session.commit()
        if len(memberships) < PAGE_SIZE:
            break
    return failed


async def sweep_expired_deadlines(sessions: SessionFactory) -> dict[str, int]:
    """The periodic entry point: one session, one sweep.

    A deadline is a promise the platform made to whoever set it, and no request
    path can keep it — the moment it matters is the moment nobody is looking.
    So a clock has to be what calls this.
    """
    async with sessions() as session:
        return {"failed": await check_and_fail_expired_deadlines(session)}
