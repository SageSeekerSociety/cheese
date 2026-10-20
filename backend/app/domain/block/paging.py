"""一条对话的时间线怎么切片读：整段、一页、计数 —— 以及共用的节流口径。

`BlockPage` 与 `_narrow()` 在这里：前者是翻页返回的形状，后者是这一组读法共用的收窄
口径（kind / author / shown）。它们原先住在 `repositories.py` 顶层，和用它的读法挤在
同一个文件里。

拆开的理由写在 `repositories.py` 的模块说明里（文件体积，不是分层）：方法签名与 SQL
逐字未动，`BlockRepository` 继承这里的 `PagingQueries`。

`repositories.py` 把 `BlockPage` 再导出一次 —— 仓库内外按
`from app.domain.block.repositories import BlockPage` 取它，拆文件不该让调用点跟着改。

和 `history.py` 的分界：这一份问「把这条时间线切开来读」，那些读法都走 `BlockPage` /
`_narrow`（`page_for_topic` 是游标翻页，`holds` 是「这一段还在不在」）；`history.py`
问的是「最近说过什么、某一条之前说过什么」，结果是裸的块列表或一个块。`between` 看着
像切片，但它没有节流、也不翻页，跟着问它的那几个走。
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import (
    Text,
    and_,
    cast,
    func,
    or_,
    select,
    text,
    tuple_,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.indexed_rows import (
    SHOWN_ROWS,
)
from app.domain.block.models import (
    Block,
    BlockKind,
)
from app.domain.conversation.services import of_room


@dataclass(frozen=True)
class BlockPage:
    """A chronological window; has_more follows the requested cursor direction."""

    items: list[Block]
    has_more: bool


def _narrow(
    stmt,
    *,
    kinds: Collection[BlockKind] | None = None,
    author: str | None = None,
    shown: bool | None = None,
):
    """A conversation read narrowed the way `GET /topics/{id}/blocks` filters:
    to some kinds, to one author, and to what the room shows (`shown=True`) or
    keeps out of it (`False`). `SHOWN_ROWS` goes in as its literal text, so a
    shown read can use `ix_blocks_shown`."""
    # The rows #4448 retired are read from session_records, never from blocks:
    # hidden event blocks holding a tool call, a subagent's conclusion, or a
    # progress line. Hidden event blocks written for other reasons — an
    # agent's notice, the line beside a message naming nobody — are still live
    # input (`shown=False` is how they are read), so the filter asks for the
    # retired writers' shape rather than for `in_room: false` alone.
    retired_site_row = and_(
        Block.kind == BlockKind.event,
        ~Block.meta["in_room"].as_string().is_distinct_from("false"),
        or_(
            cast(Block.meta, JSONB).has_key("tool"),
            cast(Block.meta, JSONB).has_key("subagent"),
            Block.meta["progress"].as_boolean().is_(True),
        ),
    )
    stmt = stmt.where(~retired_site_row)
    if kinds is not None:
        stmt = stmt.where(Block.kind.in_(list(kinds)))
    if author is not None:
        stmt = stmt.where(Block.author == author)
    if shown is not None:
        stmt = stmt.where(SHOWN_ROWS if shown else text(f"NOT ({SHOWN_ROWS.text})"))
    return stmt


class PagingQueries:
    """一条对话的时间线怎么切片读；`BlockPage` 与 `_narrow()` 在模块顶层。

    没有 `__init__`：session 由 `BlockRepository.__init__` 装上来；这些方法本来
    就只碰 `self._session` 和类属性，放在基类里与放在原来的类里是同一件事。
    """

    _session: AsyncSession

    async def list_for_topic(
        self,
        conversation_id: uuid.UUID,
        *,
        kinds: Collection[BlockKind] | None = None,
        author: str | None = None,
        shown: bool | None = None,
    ) -> list[Block]:
        """Timeline view: blocks of a topic, oldest first (spec §5), narrowed
        as `_narrow` says.

        Excludes doc_node tree blocks and inline comments — those belong to the
        document view, not the conversation timeline.

        Ties on created_at break by id, the same total order `page_for_topic`
        walks — otherwise the full view and the paged view could disagree about
        the order of blocks stamped in the same instant.
        """
        stmt = select(Block).where(Block.conversation_id == conversation_id)
        stmt = _narrow(stmt, kinds=kinds, author=author, shown=shown)
        stmt = stmt.order_by(Block.created_at, Block.id)
        return list((await self._session.scalars(stmt)).all())

    async def page_for_topic(
        self,
        conversation_id: uuid.UUID,
        *,
        limit: int,
        before: Block | None = None,
        kinds: Collection[BlockKind] | None = None,
        after: Block | None = None,
        query: str | None = None,
        reply_to: uuid.UUID | None = None,
        author: str | None = None,
        whole_room: uuid.UUID | None = None,
        also: Collection[uuid.UUID] = (),
        shown: bool | None = None,
        stored_after: int | None = None,
    ) -> BlockPage:
        """The newest `limit` blocks, or a page before/after a cursor.

        ``stored_after`` reads the blocks stored after the one numbered so
        (`Block.seq`), in the order they were stored: what a page that holds
        everything up to that number has not seen. Not the same as ``after``,
        which goes by date: a block dated before the cursor but stored after it
        is in this page and not in that one.

        ``whole_room`` reads every conversation of that room instead of one —
        its main line, its 支线 and its tasks — for a search that has to find
        what was settled somewhere else in the channel.

        ``also`` adds blocks stored under another conversation, so a 支线's
        reading starts with the message it hangs under and the files sent with
        it: they stay in the main line and they are the 支线's own first input.
        A ``whole_room`` read already covers those blocks, and ignores it.

        An after cursor reads the oldest newer records first, so catching up
        through multiple pages cannot skip intervening messages. Explicit kinds
        include document-view records; otherwise the timeline exclusions apply.

        Cursor, not offset, because chat grows at the tail while you read it: an
        offset window slides every time a message lands, so page 2 re-serves or
        skips rows. A cursor anchored on a real block is immune to tail inserts.

        The order key is the (created_at, id) pair, not created_at alone —
        created_at is stamped in Python, so a burst of streamed blocks can share
        a timestamp and single-column ordering wouldn't be a total order (the
        cursor could then skip or repeat the tied rows).
        """
        if whole_room is not None:
            scope = of_room(Block.conversation_id, whole_room)
        elif also:
            scope = or_(
                Block.conversation_id == conversation_id,
                Block.id.in_(list(also)),
            )
        else:
            scope = Block.conversation_id == conversation_id
        stmt = select(Block).where(scope)
        # 现场 wants events and nothing else; narrowing HERE rather than in the
        # caller is the difference between paging and pretending to — filtering
        # a page after the fact returns fewer rows than asked for and reports
        # has_more against the wrong set.
        stmt = _narrow(stmt, kinds=kinds, author=author, shown=shown)
        if query:
            # Literal matching: a pasted log containing % or _ is not SQL syntax.
            pattern = (
                "%"
                + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
            stmt = stmt.where(
                or_(
                    Block.content.ilike(pattern, escape="\\"),
                    # JSONB renders stored Unicode escapes as searchable text.
                    cast(cast(Block.meta, JSONB), Text).ilike(pattern, escape="\\"),
                )
            )
        if reply_to is not None:
            stmt = stmt.where(Block.reply_to == reply_to)
        if before is not None:
            # Row-value comparison: `(created_at, id) < (:ts, :id)` in one go,
            # so the cursor test matches the ORDER BY key exactly. There is no
            # composite index on (conversation_id, created_at, id) today — the
            # conversation_id index narrows to one conversation's rows and
            # Postgres sorts those (a few thousand at worst). Paging's win is the
            # payload, not the scan.
            stmt = stmt.where(
                tuple_(Block.created_at, Block.id) < (before.created_at, before.id)
            )
        if after is not None:
            stmt = stmt.where(
                tuple_(Block.created_at, Block.id) > (after.created_at, after.id)
            )
        if stored_after is not None:
            stmt = stmt.where(Block.seq > stored_after)
        # One row past the window tells us whether more blocks remain, without
        # a second COUNT query.
        if stored_after is not None:
            order: tuple = (Block.seq,)
        elif after is not None:
            order = (Block.created_at, Block.id)
        else:
            order = (Block.created_at.desc(), Block.id.desc())
        stmt = stmt.order_by(*order).limit(limit + 1)
        rows = list((await self._session.scalars(stmt)).all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        if after is None and stored_after is None:
            rows.reverse()  # callers render oldest-first, same as list_for_topic
        return BlockPage(items=rows, has_more=has_more)

    async def count_for_topic(
        self,
        conversation_id: uuid.UUID,
        *,
        kinds: Collection[BlockKind] | None = None,
        author: str | None = None,
        shown: bool | None = None,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(Block)
            .where(Block.conversation_id == conversation_id)
        )
        stmt = _narrow(stmt, kinds=kinds, author=author, shown=shown)
        return int((await self._session.scalar(stmt)) or 0)

    async def holds(
        self,
        block: Block,
        *,
        kinds: Collection[BlockKind] | None = None,
        author: str | None = None,
        shown: bool | None = None,
    ) -> bool:
        """Whether ``block`` is one a read narrowed this way would return."""
        stmt = _narrow(
            select(Block.id).where(Block.id == block.id),
            kinds=kinds,
            author=author,
            shown=shown,
        )
        return (await self._session.scalar(stmt)) is not None

    async def count_messages(
        self,
        conversation_id: uuid.UUID,
        *,
        excluding: Collection[uuid.UUID] = (),
    ) -> int:
        """Chat messages in one conversation, leaving out ``excluding``."""
        stmt = (
            select(func.count())
            .select_from(Block)
            .where(
                Block.conversation_id == conversation_id,
                Block.kind == BlockKind.message,
            )
        )
        if excluding:
            stmt = stmt.where(Block.id.not_in(list(excluding)))
        return int((await self._session.scalar(stmt)) or 0)
