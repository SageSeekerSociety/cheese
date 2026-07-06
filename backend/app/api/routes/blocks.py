"""Human-facing read routes for blocks and the document projection (docs-out).

The read half of the document feature: agents (and humans) write DOCUMENT blocks;
these routes let humans view a single block or render its struct_parent subtree
into a document tree. Access is gated on the block's project.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes._shared import require_project_access
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.block.models import Block
from app.domain.block.repositories import BlockRepository
from app.domain.block.services import BlockService
from app.domain.document.services import DocumentNode, DocumentService

router = APIRouter(prefix="/blocks", tags=["Blocks"])


def _block_to_api(b: Block) -> dict:
    return {
        "id": b.id,
        "threadId": b.thread_id,
        "projectId": b.project_id,
        "kind": b.kind,
        "content": b.content,
        "authorId": b.author_id,
        "authorKind": b.author_kind,
        "replyToId": b.reply_to_id,
        "structParentId": b.struct_parent_id,
        "createdAt": int(b.created_at.timestamp() * 1000) if b.created_at else 0,
    }


def _doc_node_to_api(node: DocumentNode) -> dict:
    return {
        "block": _block_to_api(node.block),
        "children": [_doc_node_to_api(c) for c in node.children],
    }


@router.get("/{blockId}", summary="Query Block")
async def get_block(
    block_id: Annotated[int, Path(alias="blockId", ge=1)],
    db: AsyncSession = Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    block = await BlockService(BlockRepository(db)).get_block(block_id)
    await require_project_access(db, auth_user.user_id, block.project_id)
    return {"code": 200, "message": "success", "data": {"block": _block_to_api(block)}}


@router.get("/{blockId}/document", summary="Render Document Tree")
async def get_document(
    block_id: Annotated[int, Path(alias="blockId", ge=1)],
    db: AsyncSession = Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    block = await BlockService(BlockRepository(db)).get_block(block_id)
    await require_project_access(db, auth_user.user_id, block.project_id)
    tree = await DocumentService(BlockRepository(db)).get_tree(block_id)
    return {"code": 200, "message": "success", "data": {"document": _doc_node_to_api(tree)}}
