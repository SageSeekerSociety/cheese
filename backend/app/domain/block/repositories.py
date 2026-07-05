"""Data access for blocks. No business logic (see CLAUDE.md layering)."""

from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import AuthorKind, Block, BlockKind, BlockRef


class BlockRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        project_id: int,
        kind: BlockKind,
        content: str,
        author_id: int,
        author_kind: AuthorKind = AuthorKind.USER,
        thread_id: int | None = None,
        reply_to_id: int | None = None,
        struct_parent_id: int | None = None,
    ) -> Block:
        now = datetime.now(UTC)
        block = Block(
            project_id=project_id,
            thread_id=thread_id,
            kind=kind.value,
            content=content,
            author_id=author_id,
            author_kind=author_kind.value,
            reply_to_id=reply_to_id,
            struct_parent_id=struct_parent_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(block)
        await self._session.flush()
        return block

    async def get_by_id(self, block_id: int) -> Block | None:
        stmt: Select[tuple[Block]] = select(Block).where(
            Block.id == block_id, Block.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_replies(self, reply_to_id: int) -> list[Block]:
        """Direct children on the conversation tree, oldest first."""
        stmt: Select[tuple[Block]] = (
            select(Block)
            .where(Block.reply_to_id == reply_to_id, Block.deleted_at.is_(None))
            .order_by(Block.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_children(self, struct_parent_id: int) -> list[Block]:
        """Direct children on the document tree, in structural order."""
        stmt: Select[tuple[Block]] = (
            select(Block)
            .where(Block.struct_parent_id == struct_parent_id, Block.deleted_at.is_(None))
            .order_by(Block.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_thread(self, thread_id: int, *, after_id: int = 0, limit: int = 50) -> list[Block]:
        """Blocks in a thread with id > after_id (the attention cursor), oldest first."""
        stmt: Select[tuple[Block]] = (
            select(Block)
            .where(
                Block.thread_id == thread_id,
                Block.id > after_id,
                Block.deleted_at.is_(None),
            )
            .order_by(Block.id.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def soft_delete(self, block: Block) -> None:
        block.deleted_at = datetime.now(UTC)
        await self._session.flush()

    # --- refs -------------------------------------------------------------

    async def add_ref(
        self,
        *,
        from_block_id: int,
        to_target_type: str,
        to_target_id: int,
        rel: str | None = None,
    ) -> BlockRef:
        now = datetime.now(UTC)
        ref = BlockRef(
            from_block_id=from_block_id,
            to_target_type=to_target_type,
            to_target_id=to_target_id,
            rel=rel,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(ref)
        await self._session.flush()
        return ref

    async def list_refs(self, from_block_id: int) -> list[BlockRef]:
        stmt: Select[tuple[BlockRef]] = (
            select(BlockRef)
            .where(BlockRef.from_block_id == from_block_id, BlockRef.deleted_at.is_(None))
            .order_by(BlockRef.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
