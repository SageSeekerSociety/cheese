"""Timeline effects of identity-verified native input evidence."""

from app.domain.block.repositories import BlockRepository


async def mark_read_failed(session, block_ids, failure):
    """Say on read blocks that the work they were read into failed; never
    commits. Delivery owns which inputs failed; this owns the blocks' mark."""
    await BlockRepository(session).mark_read_failed(list(block_ids), failure)


async def apply_input_echo(session, *, consumed_ids, work_id, seen_ids, seen_by):
    """Apply verified echo effects to already locked blocks without committing.

    Delivery authenticates the receiver and locks all affected IDs before calling.
    Seen reactions and attributable consumption share its settlement transaction;
    acceptance alone must never call this entry. Returns no ORM rows.
    """
    blocks = BlockRepository(session)
    if work_id is not None:
        await blocks.mark_consumed(list(consumed_ids), work_id)
    if seen_ids:
        await blocks.add_reactions_if_absent(list(seen_ids), "👀", seen_by)
