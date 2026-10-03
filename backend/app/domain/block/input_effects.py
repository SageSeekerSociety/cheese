"""Timeline effects of identity-verified native input evidence."""

from app.domain.block.repositories import BlockRepository


async def consume_input_blocks(session, block_ids, work_id):
    """Mark owned block UUIDs consumed; return no rows and never commit.

    Delivery owns evidence authentication, stable block locking and the enclosing
    transaction. This entry owns the block mutation, not input ownership release.
    """
    await BlockRepository(session).mark_consumed(list(block_ids), work_id)


async def apply_input_echo(session, *, consumed_ids, work_id, seen_ids, seen_by):
    """Apply verified echo effects to already locked blocks without committing.

    Delivery authenticates the receiver and locks all affected IDs before calling.
    Seen reactions and attributable consumption share its settlement transaction;
    acceptance alone must never call this entry. Returns no ORM rows.
    """
    blocks = BlockRepository(session)
    if work_id is not None:
        await blocks.mark_consumed(list(consumed_ids), work_id)
    for block_id in seen_ids:
        await blocks.add_reaction_if_absent(block_id, "👀", seen_by)
