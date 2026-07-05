"""Document (活文档): a projection of blocks along the struct_parent tree.

A document is NOT an independent table (architecture doc §3) — it is the
``struct_parent`` subtree rooted at a block, assembled on demand. docs-in is
just appending DOCUMENT blocks (BlockService.create_block); docs-out is this
service, which reads the subtree and renders it. Blocks are append-only and a
child's struct_parent must already exist, so the tree is acyclic by
construction; a depth guard is kept as defence in depth.
"""

from dataclasses import dataclass, field

from app.core.errors import NotFoundError
from app.domain.block.models import Block
from app.domain.block.repositories import BlockRepository

_MAX_DEPTH = 64


@dataclass
class DocumentNode:
    block: Block
    children: list["DocumentNode"] = field(default_factory=list)


class DocumentService:
    def __init__(self, block_repo: BlockRepository) -> None:
        self._blocks = block_repo

    async def get_tree(self, root_block_id: int) -> DocumentNode:
        root = await self._blocks.get_by_id(root_block_id)
        if root is None:
            raise NotFoundError(f"Block {root_block_id} not found")
        return await self._build(root, _MAX_DEPTH)

    async def _build(self, block: Block, depth: int) -> DocumentNode:
        node = DocumentNode(block=block)
        if depth <= 0:
            return node
        for child in await self._blocks.list_children(block.id):
            node.children.append(await self._build(child, depth - 1))
        return node

    async def render_text(self, root_block_id: int) -> str:
        """Flatten the document tree into indented plain text (docs-out)."""
        tree = await self.get_tree(root_block_id)
        lines: list[str] = []

        def walk(node: DocumentNode, indent: int) -> None:
            lines.append("  " * indent + node.block.content)
            for child in node.children:
                walk(child, indent + 1)

        walk(tree, 0)
        return "\n".join(lines)
