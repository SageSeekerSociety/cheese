"""Block data access."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    ) -> Block:
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
        )
        self._session.add(block)
        await self._session.flush()
        await self._session.refresh(block)
        return block

    async def get(self, block_id: uuid.UUID) -> Block | None:
        return await self._session.get(Block, block_id)

    async def set_upgraded_to_topic(self, block: Block, topic_id: uuid.UUID) -> None:
        block.upgraded_to_topic_id = topic_id
        await self._session.flush()

    async def update_content(self, block: Block, content: str) -> Block:
        block.content = content
        await self._session.flush()
        await self._session.refresh(block)
        return block

    async def list_docs_for_topic(self, topic_id: uuid.UUID) -> list[Block]:
        """Document-tree view: doc blocks of a topic (spec §5 struct tree)."""
        stmt = (
            select(Block)
            .where(Block.topic_id == topic_id, Block.kind == BlockKind.doc)
            .order_by(Block.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_for_topic(self, topic_id: uuid.UUID) -> list[Block]:
        """Timeline view: blocks of a topic, oldest first (spec §5)."""
        stmt = (
            select(Block).where(Block.topic_id == topic_id).order_by(Block.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def count_for_topic(self, topic_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(Block).where(Block.topic_id == topic_id)
        return int((await self._session.scalar(stmt)) or 0)

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
