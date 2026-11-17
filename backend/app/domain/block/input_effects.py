"""Timeline effects of identity-verified native input evidence."""

from app.domain.block.repositories import BlockRepository


async def mark_read_failed(session, block_ids, failure):
    """Say on read blocks that the work they were read into failed; never
    commits. Delivery owns which inputs failed; this owns the blocks' mark."""
    await BlockRepository(session).mark_read_failed(list(block_ids), failure)


async def apply_input_echo(session, *, consumed_ids, work_id):
    """Apply verified echo effects to already locked blocks without committing.

    Delivery authenticates the receiver and locks all affected IDs before calling.
    Attributable consumption shares its settlement transaction; acceptance alone
    must never call this entry. Returns no ORM rows. Reading a message puts no
    reaction on it: what an AI teammate reacts with is its own to choose
    (`chat_react`)."""
    if work_id is not None:
        await BlockRepository(session).mark_consumed(list(consumed_ids), work_id)
