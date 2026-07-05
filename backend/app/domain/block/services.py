"""Business logic for blocks. Enforces the append-only + tree invariants."""

from app.core.errors import BadRequestError, NotFoundError
from app.domain.block.models import AuthorKind, Block, BlockKind, BlockRef
from app.domain.block.repositories import BlockRepository


class BlockService:
    def __init__(self, repo: BlockRepository) -> None:
        self._repo = repo

    async def _require_block(self, block_id: int) -> Block:
        block = await self._repo.get_by_id(block_id)
        if block is None:
            raise NotFoundError(f"Block {block_id} not found")
        return block

    async def create_block(
        self,
        *,
        project_id: int,
        content: str,
        author_id: int,
        author_kind: AuthorKind = AuthorKind.USER,
        kind: BlockKind = BlockKind.MESSAGE,
        thread_id: int | None = None,
        reply_to_id: int | None = None,
        struct_parent_id: int | None = None,
    ) -> Block:
        # Parents must exist and belong to the same project — a block cannot
        # reach across project boundaries (the aggregate-root invariant).
        if reply_to_id is not None:
            parent = await self._require_block(reply_to_id)
            if parent.project_id != project_id:
                raise BadRequestError("reply_to block belongs to another project")
        if struct_parent_id is not None:
            parent = await self._require_block(struct_parent_id)
            if parent.project_id != project_id:
                raise BadRequestError("struct_parent block belongs to another project")
        return await self._repo.create(
            project_id=project_id,
            kind=kind,
            content=content,
            author_id=author_id,
            author_kind=author_kind,
            thread_id=thread_id,
            reply_to_id=reply_to_id,
            struct_parent_id=struct_parent_id,
        )

    async def get_block(self, block_id: int) -> Block:
        return await self._require_block(block_id)

    async def list_replies(self, block_id: int) -> list[Block]:
        await self._require_block(block_id)
        return await self._repo.list_replies(block_id)

    async def list_children(self, block_id: int) -> list[Block]:
        await self._require_block(block_id)
        return await self._repo.list_children(block_id)

    async def list_thread(self, thread_id: int, *, after_id: int = 0, limit: int = 50) -> list[Block]:
        return await self._repo.list_thread(thread_id, after_id=after_id, limit=limit)

    async def add_ref(
        self,
        *,
        from_block_id: int,
        to_target_type: str,
        to_target_id: int,
        rel: str | None = None,
    ) -> BlockRef:
        await self._require_block(from_block_id)
        if not to_target_type:
            raise BadRequestError("to_target_type is required")
        return await self._repo.add_ref(
            from_block_id=from_block_id,
            to_target_type=to_target_type,
            to_target_id=to_target_id,
            rel=rel,
        )

    async def list_refs(self, block_id: int) -> list[BlockRef]:
        await self._require_block(block_id)
        return await self._repo.list_refs(block_id)
