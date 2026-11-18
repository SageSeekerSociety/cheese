"""已读：谁真的读到了一段对话里的哪几条消息。

A person has read a message once it was on their screen while the page was in
the foreground (the page reports the block numbers it showed); an AI teammate
has read one once it was part of what a turn of its was given. Each reader's
reads of one conversation are one row of `MessageRead`: the stretches of block
numbers (`Block.seq`) read, as a multirange.

Every write merges into the stretches already there, so reporting a number
twice changes nothing, and a stretch also swallows the gap to its neighbour
when nobody else said anything in that gap — the steps, notices and the
reader's own words between two messages are nothing to read, and leaving them
as holes would split one reader's row into a stretch per message. A gap that
holds a message somebody else wrote stays a gap: that message was not read.

Anyone who can read the conversation sees who has read what in it; this module
does not decide that, its callers' doors do.
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from sqlalchemy import Integer, bindparam, delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import ARRAY, BIGINT, INT8MULTIRANGE, Range
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_frames import show_once_committed
from app.domain.block.authorship import participant_blocks
from app.domain.block.models import Block, BlockKind, MessageRead

#: How many stretches one report may carry. A page reports what it showed in
#: the last few seconds, which is a handful; more is not a page reading.
MAX_REPORTED_RANGES = 200

#: The kinds of block a reader reads: what a conversation shows as a message.
_READABLE = (BlockKind.message, BlockKind.attachment, BlockKind.artifact)

Stretch = tuple[int, int]
"""Block numbers ``first..last``, both included."""


def merged(stretches: Iterable[Stretch]) -> list[Stretch]:
    """``stretches`` as the fewest that cover the same numbers, in order:
    overlapping and touching ones become one."""
    out: list[Stretch] = []
    for first, last in sorted(stretches):
        if out and first <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], last))
        else:
            out.append((first, last))
    return out


def _stretches(ranges: Sequence[Range[int]] | None) -> list[Stretch]:
    """A multirange read back, as inclusive stretches. PostgreSQL keeps an
    integer range canonical: ``[lower, upper)``."""
    return [
        (r.lower, r.upper - 1)
        for r in ranges or []
        if not r.isempty and r.lower is not None and r.upper is not None
    ]


def _ranges(stretches: Iterable[Stretch]) -> list[Range[int]]:
    return [Range(first, last + 1, bounds="[)") for first, last in stretches]


async def _gaps_with_unread(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    reader: str,
    gaps: list[Stretch],
) -> set[int]:
    """Which of ``gaps`` (block numbers strictly between, given as the numbers
    either side) hold something somebody other than ``reader`` said."""
    if not gaps:
        return set()
    table = (
        func.unnest(
            bindparam("gap_starts", [lo for lo, _ in gaps], type_=ARRAY(BIGINT)),
            bindparam("gap_ends", [hi for _, hi in gaps], type_=ARRAY(BIGINT)),
        )
        .table_valued("lo", "hi", with_ordinality="i")
        .render_derived(name="gaps")
    )
    said = exists(
        select(Block.id).where(
            Block.conversation_id == conversation_id,
            Block.kind.in_(_READABLE),
            participant_blocks(),
            Block.author != reader,
            Block.seq > table.c.lo,
            Block.seq < table.c.hi,
        )
    )
    rows = await session.scalars(
        select(func.cast(table.c.i, Integer)).select_from(table).where(said)
    )
    return {int(i) - 1 for i in rows}


async def _newest_seq(session: AsyncSession, conversation_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.coalesce(func.max(Block.seq), 0)).where(
                Block.conversation_id == conversation_id
            )
        )
        or 0
    )


async def record(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    reader: str,
    stretches: Iterable[Stretch],
) -> list[Stretch]:
    """``reader`` has read these block numbers of ``conversation_id``.

    Merged into what they had read, under a lock on their row so two reports
    at once both land. Numbers past the conversation's newest block are not
    readable yet and are dropped. Returns the reader's stretches that grew —
    what the pages open on the conversation are told (a ``reads`` frame once
    this commits) — or nothing when every number was already read.
    Never commits.
    """
    newest = await _newest_seq(session, conversation_id)
    wanted = merged(
        (max(first, 1), min(last, newest))
        for first, last in stretches
        if first <= last and first <= newest and last >= 1
    )
    if not wanted:
        return []
    # The row to lock. Its first write is an empty one: the merge below then
    # always runs on a row this transaction holds.
    await session.execute(
        pg_insert(MessageRead)
        .values(
            conversation_id=conversation_id,
            reader=reader,
            seqs=[],
            updated_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing()
    )
    held = _stretches(
        await session.scalar(
            select(MessageRead.seqs)
            .where(
                MessageRead.conversation_id == conversation_id,
                MessageRead.reader == reader,
            )
            .with_for_update()
        )
    )
    after = merged([*held, *wanted])
    if after == merged(held):
        return []
    # Close the gaps beside every stretch that took new numbers, where nobody
    # else said anything in between.
    touched = {
        i
        for i, (first, last) in enumerate(after)
        if any(first <= w_first and w_last <= last for w_first, w_last in wanted)
    }
    gaps = sorted(
        {i - 1 for i in touched if i > 0} | {i for i in touched if i + 1 < len(after)}
    )
    blocked = await _gaps_with_unread(
        session,
        conversation_id,
        reader,
        [(after[i][1], after[i + 1][0]) for i in gaps],
    )
    bridged = {gap for n, gap in enumerate(gaps) if n not in blocked}
    joined: list[Stretch] = []
    grew: list[Stretch] = []
    for i, stretch in enumerate(after):
        if i - 1 in bridged:
            joined[-1] = (joined[-1][0], stretch[1])
        else:
            joined.append(stretch)
    for stretch in joined:
        if stretch not in held:
            grew.append(stretch)
    await session.execute(
        update(MessageRead)
        .where(
            MessageRead.conversation_id == conversation_id,
            MessageRead.reader == reader,
        )
        .values(seqs=_ranges(joined), updated_at=func.now())
    )
    show_once_committed(
        session,
        conversation_id,
        {
            "type": "reads",
            "conversation_id": str(conversation_id),
            "reader": reader,
            "ranges": [list(s) for s in grew],
        },
    )
    return grew


async def record_blocks(
    session: AsyncSession, reader: str, block_ids: Iterable[uuid.UUID]
) -> None:
    """``reader`` has read these blocks, wherever each was said: a 支线's turn
    reads the message it hangs under, which is the channel's. Never commits."""
    ids = list(dict.fromkeys(block_ids))
    if not ids:
        return
    rows = (
        await session.execute(
            select(Block.conversation_id, Block.seq).where(Block.id.in_(ids))
        )
    ).all()
    by_conversation: dict[uuid.UUID, list[Stretch]] = {}
    for conversation_id, seq in rows:
        by_conversation.setdefault(conversation_id, []).append((seq, seq))
    # One conversation after another, in one order, so two writers never hold
    # each other's rows.
    for conversation_id in sorted(by_conversation):
        await record(session, conversation_id, reader, by_conversation[conversation_id])


async def reads_between(
    session: AsyncSession, conversation_id: uuid.UUID, first: int, last: int
) -> list[dict]:
    """Who has read which of block numbers ``first..last`` here: per reader,
    their stretches within that span. A reader who read none of it is left
    out."""
    span = func.int8multirange(func.int8range(first, last, "[]"), type_=INT8MULTIRANGE)
    clipped = MessageRead.seqs.op("*", return_type=INT8MULTIRANGE)(span).label("seqs")
    rows = (
        await session.execute(
            select(MessageRead.reader, clipped)
            .where(
                MessageRead.conversation_id == conversation_id,
                MessageRead.seqs.op("&&", is_comparison=True)(span),
            )
            .order_by(MessageRead.reader)
        )
    ).all()
    return [
        {"reader": reader, "ranges": [list(s) for s in _stretches(seqs)]}
        for reader, seqs in rows
    ]


async def forget_reader(session: AsyncSession, reader: str) -> None:
    """Every read of ``reader``'s, gone (a closed account). Never commits."""
    await session.execute(delete(MessageRead).where(MessageRead.reader == reader))
