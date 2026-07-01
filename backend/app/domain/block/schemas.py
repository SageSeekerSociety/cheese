"""Block response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.block.models import AuthorType, BlockKind


class BlockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    topic_id: uuid.UUID
    kind: BlockKind
    author_type: AuthorType
    author: str
    content: str
    # 双树 + 引用 (spec §5): reply_to = 对话树, struct_parent = 文档树, refs[] =
    # 引用(决策/PR/现场); upgraded_to_topic_id makes an upgraded block a live link.
    reply_to: uuid.UUID | None
    struct_parent: uuid.UUID | None = None
    # B1 doc tree: structural type + sibling order (set only on doc_node blocks).
    node_type: str | None = None
    struct_order: float | None = None
    # B4 段落评论: the quoted text a comment was selected on (set on comment blocks).
    anchor_quote: str | None = None
    refs: list[str] = []
    upgraded_to_topic_id: uuid.UUID | None = None
    # The agent turn that produced this block (R4): groups a turn's blocks.
    turn_id: uuid.UUID | None = None
    created_at: datetime
