"""`live_inputs` reads the turn's blocks in at most two queries, not 2N gets.

It sits on the first-byte path of a turn; one `session.get` per input block (and
one more per quote) was a serial round trip each.
"""

import asyncio
import uuid
from types import SimpleNamespace

from app.domain.agent.prompt import live_inputs
from app.domain.block.models import BlockKind


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return iter(self._rows)


class _CountingSession:
    def __init__(self, blocks):
        self.blocks = {b.id: b for b in blocks}
        self.gets = 0
        self.executes = 0

    async def get(self, _model, block_id):
        self.gets += 1
        return self.blocks.get(block_id)

    async def execute(self, stmt):
        self.executes += 1
        wanted = set(stmt.whereclause.right.value)
        return _Rows([b for i, b in self.blocks.items() if i in wanted])


def _block(kind=BlockKind.message, reply_to=None):
    return SimpleNamespace(id=uuid.uuid4(), kind=kind, reply_to=reply_to)


async def _one_by_one(session, block_ids):
    """The reference: what `live_inputs` returned before it batched."""
    stored = replied = None
    for block_id in block_ids:
        block = await session.get(None, block_id)
        if block is not None:
            if block.kind == BlockKind.message:
                stored = block
            if block.reply_to is not None:
                replied = await session.get(None, block.reply_to)
    return stored, replied


def _ten_with_replies():
    quoted = [_block() for _ in range(10)]
    inputs = [_block(reply_to=q.id) for q in quoted]
    return quoted + inputs, [b.id for b in inputs]


def test_ten_quoting_inputs_cost_no_gets_and_at_most_two_queries():
    blocks, ids = _ten_with_replies()
    session = _CountingSession(blocks)

    got = asyncio.run(live_inputs(session, ids))

    assert session.gets == 0
    assert session.executes <= 2
    assert got == asyncio.run(_one_by_one(_CountingSession(blocks), ids))


def test_missing_ids_and_non_message_blocks_match_the_one_by_one_answer():
    quoted = _block()
    note = _block(kind=BlockKind.event, reply_to=quoted.id)
    msg = _block(reply_to=uuid.uuid4())  # quote row is gone
    blocks = [quoted, note, msg]
    for ids in (
        [uuid.uuid4()],
        [note.id],
        [note.id, msg.id],
        [msg.id, note.id],
        [msg.id, uuid.uuid4(), msg.id],
        [],
    ):
        got = asyncio.run(live_inputs(_CountingSession(blocks), ids))
        want = asyncio.run(_one_by_one(_CountingSession(blocks), ids))
        assert got == want, ids
