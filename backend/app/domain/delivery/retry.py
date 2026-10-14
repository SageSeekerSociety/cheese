"""Input ownership reconciliation for an explicitly requested failed-work retry."""

from sqlalchemy import select

from app.domain.delivery.models import NativeInput


async def failed_work_inputs(session, *, conversation_id, work_id):
    """Lock this exact failed work, before claiming its retry card.

    A terminated input frees the seat but deliberately keeps its batch held.
    User-authorized retry may re-admit that batch; it must never manufacture
    successful completion or reset a delivery's transport outcome.
    """
    return list(
        await session.scalars(
            select(NativeInput)
            .where(
                NativeInput.conversation_id == conversation_id,
                (NativeInput.execution_work_id == work_id)
                | (NativeInput.work_id == work_id),
            )
            .order_by(NativeInput.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def can_retry_inputs(rows):
    return bool(rows) and all(
        row.completed_at is None
        and row.terminated_at is not None
        and row.termination == "is_error"
        for row in rows
    )


def release_for_explicit_retry(rows):
    """Transfer batch eligibility, not receipt or completion facts.

    Called only after atomically claiming the exact failed card. Receipt stamps,
    termination and Delivery state remain unchanged. The next registered input
    owns any still-unconsumed blocks and releases them on successful completion.
    """
    if not can_retry_inputs(rows):
        # i18n-exempt: internal invariant; the caller checks eligibility first.
        raise ValueError("Retry requires a confirmed failed input interval")
    for row in rows:
        row.released_block_ids = sorted(
            set(row.released_block_ids) | set(row.held_block_ids)
        )
