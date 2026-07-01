"""Block data access."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.turn_context import current_turn_id
from app.domain.block.models import AuthorType, Block, BlockKind


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
        )
        self._session.add(block)
        await self._session.flush()
        await self._session.refresh(block)
        return block

    async def get(self, block_id: uuid.UUID) -> Block | None:
        return await self._session.get(Block, block_id)

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

    # Document-view kinds — never part of the conversation timeline.
    _NON_TIMELINE = (BlockKind.doc_node, BlockKind.comment)

    async def list_for_topic(self, topic_id: uuid.UUID) -> list[Block]:
        """Timeline view: blocks of a topic, oldest first (spec §5).

        Excludes doc_node tree blocks and inline comments — those belong to the
        document view, not the conversation timeline.
        """
        stmt = (
            select(Block)
            .where(
                Block.topic_id == topic_id,
                Block.kind.not_in(self._NON_TIMELINE),
            )
            .order_by(Block.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

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
            .where(
                Block.topic_id == topic_id, Block.kind == BlockKind.comment
            )
            .order_by(Block.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

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
