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
    """POST /blocks/{id}/reactions — Slack semantics: toggles (emoji, caller)."""

    emoji: str = Field(min_length=1, max_length=32)


class BlockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    # Its place in the order the conversation's blocks were stored (`Block.seq`):
    # a page that missed some asks for those after the largest it holds.
    seq: int
    kind: BlockKind
    author_type: AuthorType
    author: str
    content: str
    # reply_to = 对话树, refs[] = 引用(决策/PR/现场).
    reply_to: uuid.UUID | None
    # Render-by-type: mimeType of an artifact block (set on artifact blocks).
    mime_type: str | None = None
    refs: list[str] = []
    # The agent turn that produced this block (R4): groups a turn's blocks.
    turn_id: uuid.UUID | None = None
    # Structured event payload (kind=event): {"tool", "arg", "platform"} — the
    # UI translates/classifies from this; `content` is the baked-text fallback.
    meta: dict | None = None
    # Aggregated emoji reactions (Slack chips). Not a model attribute — list
    # endpoints fill it from one batch query (see reactions_for_blocks).
    reactions: list[ReactionOut] = []
    created_at: datetime
