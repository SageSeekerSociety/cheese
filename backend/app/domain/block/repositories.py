"""Data access for the block substrate (万物皆块 §2.1).

Chat messages are ``block`` rows with ``thread_id`` set. This repository is
data-access only — no business logic, no authorization.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import Block, BlockKind


class BlockRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_message(
        self,
        *,
        thread_id: int,
        project_id: int | None,
        author_id: int,
        text: str,
        reply_to_id: int | None = None,
        refs: list | None = None,
    ) -> Block:
        block = Block(
            project_id=project_id,
            thread_id=thread_id,
            kind=BlockKind.MESSAGE,
            author_id=author_id,
            reply_to_id=reply_to_id,
            refs=refs,
            content=text,
            created_at=datetime.now(UTC),
        )
        self._session.add(block)
        await self._session.flush()
        return block

    async def set_deleted(self, block: Block, *, deleted: bool = True) -> Block:
        """Soft-delete (tombstone) or restore a message. The row is kept so replies and
        quotes that reference it degrade gracefully instead of dangling."""
        block.deleted_at = datetime.now(UTC) if deleted else None
        await self._session.flush()
        return block

    async def set_pinned(self, block: Block, *, pinned: bool = True) -> Block:
        """Pin/unpin a message in its thread (Feishu-style 置顶)."""
        block.pinned_at = datetime.now(UTC) if pinned else None
        await self._session.flush()
        return block

    async def list_pinned(self, thread_id: int) -> list[Block]:
        """Pinned messages of a thread, most-recently-pinned first."""
        rows = (
            await self._session.execute(
                select(Block)
                .where(
                    Block.thread_id == thread_id,
                    Block.kind == BlockKind.MESSAGE,
                    Block.pinned_at.is_not(None),
                )
                .order_by(Block.pinned_at.desc())
            )
        ).scalars()
        return list(rows)

    async def get_message_in_thread(self, thread_id: int, block_id: int) -> Block | None:
        """Fetch a MESSAGE block by id, scoped to a thread — used to build a quoted
        preview. Returns ``None`` if the id is unknown or belongs to another thread
        (so a reply to a missing/foreign block degrades gracefully)."""
        return (
            await self._session.execute(
                select(Block).where(
                    Block.id == block_id,
                    Block.thread_id == thread_id,
                    Block.kind == BlockKind.MESSAGE,
                )
            )
        ).scalar_one_or_none()

    async def messages_since(self, thread_id: int, after_id: int = 0) -> list[Block]:
        rows = (
            await self._session.execute(
                select(Block)
                .where(
                    Block.thread_id == thread_id,
                    Block.kind == BlockKind.MESSAGE,
                    Block.id > after_id,
                )
                .order_by(Block.id)
            )
        ).scalars()
        return list(rows)

    async def latest(self, thread_id: int) -> Block | None:
        return (
            await self._session.execute(
                select(Block)
                .where(Block.thread_id == thread_id, Block.kind == BlockKind.MESSAGE)
                .order_by(Block.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    # -- document tree (万物皆块: DOC_ROOT + DOC_NODE children) ------------------

    async def get(self, block_id: int) -> Block | None:
        return await self._session.get(Block, block_id)

    async def add_doc_root(self, *, project_id: int | None, author_id: int, content: str) -> Block:
        block = Block(
            project_id=project_id,
            thread_id=None,
            kind=BlockKind.DOC_ROOT,
            author_id=author_id,
            content=content,
            created_at=datetime.now(UTC),
        )
        self._session.add(block)
        await self._session.flush()
        return block

    async def add_doc_node(
        self,
        *,
        project_id: int | None,
        author_id: int,
        struct_parent_id: int,
        content: str,
        node_type: str,
        struct_order: float,
    ) -> Block:
        block = Block(
            project_id=project_id,
            thread_id=None,
            kind=BlockKind.DOC_NODE,
            author_id=author_id,
            struct_parent_id=struct_parent_id,
            node_type=node_type,
            struct_order=struct_order,
            content=content,
            created_at=datetime.now(UTC),
        )
        self._session.add(block)
        await self._session.flush()
        return block

    async def add_event(
        self,
        *,
        project_id: int | None,
        author_id: int,
        content: str,
        refs: list | None = None,
        thread_id: int | None = None,
    ) -> Block:
        block = Block(
            project_id=project_id,
            thread_id=thread_id,
            kind=BlockKind.EVENT,
            author_id=author_id,
            content=content,
            refs=refs,
            created_at=datetime.now(UTC),
        )
        self._session.add(block)
        await self._session.flush()
        return block

    async def list_doc_nodes(self, root_id: int) -> list[Block]:
        rows = (
            await self._session.execute(
                select(Block)
                .where(Block.struct_parent_id == root_id, Block.kind == BlockKind.DOC_NODE)
                .order_by(Block.struct_order)
            )
        ).scalars()
        return list(rows)

    async def update_content(self, block: Block, content: str) -> Block:
        block.content = content
        block.edited_at = datetime.now(UTC)
        await self._session.flush()
        return block

    async def update_node(self, block: Block, *, node_type: str, struct_order: float) -> Block:
        block.node_type = node_type
        block.struct_order = struct_order
        await self._session.flush()
        return block

    async def delete(self, block: Block) -> None:
        await self._session.delete(block)
        await self._session.flush()
