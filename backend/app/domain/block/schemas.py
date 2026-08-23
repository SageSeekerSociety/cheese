"""Block response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.block.models import AuthorType, BlockKind


class ReactionOut(BaseModel):
    """One aggregated emoji reaction group on a block (Slack-style chip)."""

    emoji: str
    count: int
    authors: list[str]


class ReactionToggleIn(BaseModel):
    """POST /blocks/{id}/reactions — Slack semantics: toggles (emoji, author)."""

    emoji: str = Field(min_length=1, max_length=32)
    author: str = Field(min_length=1, max_length=128)


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
    # Render-by-type: mimeType of an artifact block (set on artifact blocks).
    mime_type: str | None = None
    # How many times the living doc has been written (kind=doc). Send it back as
    # `expected_version` to save; the write is refused if the doc moved since.
    doc_version: int = 1
    refs: list[str] = []
    upgraded_to_topic_id: uuid.UUID | None = None
    # …and the thread it was dispatched into, which is what upgrading a message
    # inside a room does. Exactly one of the two is ever set.
    upgraded_to_task_id: uuid.UUID | None = None
    # The agent turn that produced this block (R4): groups a turn's blocks.
    turn_id: uuid.UUID | None = None
    # Structured event payload (kind=event): {"tool", "arg", "platform"} — the
    # UI translates/classifies from this; `content` is the baked-text fallback.
    meta: dict | None = None
    # Aggregated emoji reactions (Slack chips). Not a model attribute — list
    # endpoints fill it from one batch query (see reactions_for_blocks).
    reactions: list[ReactionOut] = []
    created_at: datetime
