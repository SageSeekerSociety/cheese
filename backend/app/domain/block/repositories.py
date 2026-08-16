"""Block data access."""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.turn_context import current_turn_id
from app.domain.block.models import (
    CONSUMED_TURN_META_KEY,
    PROMPT_ATTEMPTS_META_KEY,
    AuthorType,
    Block,
    BlockKind,
    BlockReaction,
    prompt_attempts,
)


@dataclass(frozen=True)
class BlockPage:
    """One bottom-anchored window of a topic's timeline.

    `has_more` is about OLDER blocks only — the window always ends at the
    newest block the cursor allows, so "more" can only lie above it.
    """

    items: list[Block]
    has_more: bool


class BlockRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        author: str,
        author_type: AuthorType,
        content: str,
        kind: BlockKind = BlockKind.message,
        reply_to: uuid.UUID | None = None,
        struct_parent: uuid.UUID | None = None,
        refs: list[str] | None = None,
        node_type: str | None = None,
        struct_order: float | None = None,
        turn_id: uuid.UUID | None = None,
        anchor_quote: str | None = None,
        mime_type: str | None = None,
        meta: dict | None = None,
    ) -> Block:
        # Cheese-side handlers don't pass turn_id explicitly; fall back to the
        # ambient turn id set from the X-Cheese-Turn header (R4).
        if turn_id is None:
            turn_id = current_turn_id.get()
        block = Block(
            project_id=project_id,
            topic_id=topic_id,
            author=author,
            author_type=author_type,
            content=content,
            kind=kind,
            reply_to=reply_to,
            struct_parent=struct_parent,
            refs=refs or [],
            node_type=node_type,
            struct_order=struct_order,
            turn_id=turn_id,
            anchor_quote=anchor_quote,
            mime_type=mime_type,
            meta=meta,
        )
        self._session.add(block)
        await self._session.flush()
        await self._session.refresh(block)
        return block

    async def get(self, block_id: uuid.UUID) -> Block | None:
        return await self._session.get(Block, block_id)

    async def has_eid(self, topic_id: uuid.UUID, eid: str) -> bool:
        """Whether this topic already materialized a hook event id."""
        stmt = (
            select(Block.id)
            .where(
                Block.topic_id == topic_id,
                Block.meta["eid"].as_string() == eid,
            )
            .limit(1)
        )
        return await self._session.scalar(stmt) is not None

    async def delete(self, block: Block) -> None:
        await self._session.delete(block)
        await self._session.flush()

    async def set_upgraded_to_topic(self, block: Block, topic_id: uuid.UUID) -> None:
        block.upgraded_to_topic_id = topic_id
        await self._session.flush()

    async def update_content(self, block: Block, content: str) -> Block:
        block.content = content
        await self._session.flush()
        await self._session.refresh(block)
        return block

    async def mark_consumed(
        self, block_ids: list[uuid.UUID], turn_id: uuid.UUID
    ) -> None:
        """Stamp human blocks as read into turn `turn_id`'s prompt.

        This is what makes the next turn's pending window a fact instead of a
        guess (see CONSUMED_TURN_META_KEY). `meta` is a plain JSON column, so the
        dict is REPLACED rather than mutated in place — an in-place mutation is
        invisible to SQLAlchemy's change detection and would silently not save.
        """
        if not block_ids:
            return
        stmt = select(Block).where(Block.id.in_(block_ids))
        for block in (await self._session.scalars(stmt)).all():
            block.meta = {**(block.meta or {}), CONSUMED_TURN_META_KEY: str(turn_id)}
        await self._session.flush()

    async def bump_prompt_attempts(self, block_ids: list[uuid.UUID]) -> int:
        """Record that these blocks went into a prompt AGAIN, and return the
        highest attempt count in the batch.

        Called when the prompt is built, not when the turn ends — that is the
        whole point. `mark_consumed` runs only on a turn that finished, so a
        turn that dies leaves no trace at all and the next turn re-sends the
        identical batch. This counter is the trace.

        Same `meta` replacement rule as `mark_consumed`: in-place mutation of a
        JSON column is invisible to SQLAlchemy and would silently not save.
        """
        if not block_ids:
            return 0
        highest = 0
        stmt = select(Block).where(Block.id.in_(block_ids))
        for block in (await self._session.scalars(stmt)).all():
            n = prompt_attempts(block) + 1
            block.meta = {**(block.meta or {}), PROMPT_ATTEMPTS_META_KEY: n}
            highest = max(highest, n)
        await self._session.flush()
        return highest

    async def update_node(
        self, block: Block, *, node_type: str, struct_order: float
    ) -> Block:
        """Reposition / retype a doc-tree node (B1)."""
        block.node_type = node_type
        block.struct_order = struct_order
        await self._session.flush()
        return block

    async def doc_root(self, topic_id: uuid.UUID) -> Block | None:
        """The topic's canonical living-doc block (markdown blob, spec §2.2)."""
        stmt = (
            select(Block)
            .where(Block.topic_id == topic_id, Block.kind == BlockKind.doc)
            .order_by(Block.created_at)
        )
        return (await self._session.scalars(stmt)).first()

    async def list_doc_nodes(self, topic_id: uuid.UUID) -> list[Block]:
        """The living doc's structured node tree (B1), in document order."""
        stmt = (
            select(Block)
            .where(Block.topic_id == topic_id, Block.kind == BlockKind.doc_node)
            .order_by(Block.struct_order)
        )
        return list((await self._session.scalars(stmt)).all())

    # Document-view / render-only kinds — never part of the conversation timeline.
    # Artifacts are preview pointers surfaced in the preview window, not chat.
    _NON_TIMELINE = (BlockKind.doc_node, BlockKind.comment, BlockKind.artifact)

    async def list_for_topic(self, topic_id: uuid.UUID) -> list[Block]:
        """Timeline view: blocks of a topic, oldest first (spec §5).

        Excludes doc_node tree blocks and inline comments — those belong to the
        document view, not the conversation timeline.

        Ties on created_at break by id, the same total order `page_for_topic`
        walks — otherwise the full view and the paged view could disagree about
        the order of blocks stamped in the same instant.
        """
        stmt = (
            select(Block)
            .where(
                Block.topic_id == topic_id,
                Block.kind.not_in(self._NON_TIMELINE),
            )
            .order_by(Block.created_at, Block.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def latest_for_topic(self, topic_id: uuid.UUID) -> Block | None:
        """The newest block in the topic's timeline, or None for an empty topic.

        Same total order and same exclusions as `list_for_topic`, so "the last
        thing in the topic" means here exactly what the reader sees at the
        bottom of the conversation — which is what makes a stall verdict built
        on it checkable by hand.
        """
        stmt = (
            select(Block)
            .where(
                Block.topic_id == topic_id,
                Block.kind.not_in(self._NON_TIMELINE),
            )
            .order_by(Block.created_at.desc(), Block.id.desc())
            .limit(1)
        )
        return (await self._session.scalars(stmt)).first()

    async def page_for_topic(
        self,
        topic_id: uuid.UUID,
        *,
        limit: int,
        before: Block | None = None,
    ) -> BlockPage:
        """A bottom-anchored slice of the timeline: the newest `limit` blocks,
        or — with `before` — the `limit` blocks immediately OLDER than it.

        Cursor, not offset, because chat grows at the tail while you read it: an
        offset window slides every time a message lands, so page 2 re-serves or
        skips rows. A cursor anchored on a real block is immune to tail inserts.

        The order key is the (created_at, id) pair, not created_at alone —
        created_at is stamped in Python, so a burst of streamed blocks can share
        a timestamp and single-column ordering wouldn't be a total order (the
        cursor could then skip or repeat the tied rows).
        """
        stmt = select(Block).where(
            Block.topic_id == topic_id,
            Block.kind.not_in(self._NON_TIMELINE),
        )
        if before is not None:
            # Row-value comparison: `(created_at, id) < (:ts, :id)` in one go,
            # so the cursor test matches the ORDER BY key exactly. There is no
            # composite index on (topic_id, created_at, id) today — the topic_id
            # index narrows to one topic's rows and Postgres sorts those (a few
            # thousand at worst). Paging's win is the payload, not the scan.
            stmt = stmt.where(
                tuple_(Block.created_at, Block.id) < (before.created_at, before.id)
            )
        # One row past the window tells us whether older blocks remain, without
        # a second COUNT query.
        stmt = stmt.order_by(Block.created_at.desc(), Block.id.desc()).limit(limit + 1)
        rows = list((await self._session.scalars(stmt)).all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()  # callers render oldest-first, same as list_for_topic
        return BlockPage(items=rows, has_more=has_more)

    async def count_for_topic(self, topic_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Block)
            .where(
                Block.topic_id == topic_id,
                Block.kind.not_in(self._NON_TIMELINE),
            )
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def list_comments_for_topic(self, topic_id: uuid.UUID) -> list[Block]:
        """Inline comments (B4), oldest first; each anchors to a doc node via
        reply_to."""
        stmt = (
            select(Block)
            .where(Block.topic_id == topic_id, Block.kind == BlockKind.comment)
            .order_by(Block.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def latest_artifact(self, topic_id: uuid.UUID) -> Block | None:
        """The topic's current preview (spec §9.1): the most recent artifact block
        芝士 pointed at. Newest wins — re-running `cheese artifact` repoints it."""
        stmt = (
            select(Block)
            .where(Block.topic_id == topic_id, Block.kind == BlockKind.artifact)
            .order_by(Block.created_at.desc())
        )
        return (await self._session.scalars(stmt)).first()

    # ---- Emoji reactions (Slack semantics) ----

    async def _get_reaction(
        self, block_id: uuid.UUID, emoji: str, author: str
    ) -> BlockReaction | None:
        stmt = select(BlockReaction).where(
            BlockReaction.block_id == block_id,
            BlockReaction.emoji == emoji,
            BlockReaction.author == author,
        )
        return (await self._session.scalars(stmt)).first()

    async def toggle_reaction(
        self, block_id: uuid.UUID, emoji: str, author: str
    ) -> bool:
        """Slack-style toggle: add the (emoji, author) reaction, or remove it if
        it already exists. Returns True when added, False when removed."""
        existing = await self._get_reaction(block_id, emoji, author)
        if existing is not None:
            await self._session.delete(existing)
            await self._session.flush()
            return False
        self._session.add(BlockReaction(block_id=block_id, emoji=emoji, author=author))
        await self._session.flush()
        return True

    async def add_reaction_if_absent(
        self, block_id: uuid.UUID, emoji: str, author: str
    ) -> bool:
        """Idempotent add (never removes) — for platform receipts like 芝士's ✅
        on the message that summoned it. Returns True when a row was created."""
        if await self._get_reaction(block_id, emoji, author) is not None:
            return False
        self._session.add(BlockReaction(block_id=block_id, emoji=emoji, author=author))
        await self._session.flush()
        return True

    async def reactions_for_blocks(
        self, block_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[dict]]:
        """Aggregated reactions for many blocks in ONE query (no N+1):
        block_id → [{"emoji", "count", "authors"}], groups ordered by the emoji's
        first appearance on the block, authors by reaction time (Slack)."""
        if not block_ids:
            return {}
        stmt = (
            select(BlockReaction)
            .where(BlockReaction.block_id.in_(block_ids))
            .order_by(BlockReaction.created_at, BlockReaction.id)
        )
        rows = (await self._session.scalars(stmt)).all()
        grouped: dict[uuid.UUID, dict[str, dict]] = {}
        for r in rows:
            per_block = grouped.setdefault(r.block_id, {})
            agg = per_block.setdefault(
                r.emoji, {"emoji": r.emoji, "count": 0, "authors": []}
            )
            agg["count"] += 1
            agg["authors"].append(r.author)
        return {bid: list(per.values()) for bid, per in grouped.items()}

    async def reactions_for_block(self, block_id: uuid.UUID) -> list[dict]:
        """Aggregated reactions of one block (same shape as the batch form)."""
        agg = await self.reactions_for_blocks([block_id])
        return agg.get(block_id, [])

    async def list_by_kind_for_project(
        self, project_id: uuid.UUID, kind: BlockKind
    ) -> list[Block]:
        """Project-wide blocks of a kind, newest first — e.g. the decision log
        (kind=decision), each traceable to its source topic via refs (§7.1)."""
        stmt = (
            select(Block)
            .where(Block.project_id == project_id, Block.kind == kind)
            .order_by(Block.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())
