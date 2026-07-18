"""Block model — 万物皆块 (spec §5).

Every piece of content (a message, a doc node, a decision, an attachment) is a
Block. Blocks live in one pool per project and are organized by two trees:

- reply_to     → conversation tree ("how it was discussed")
- struct_parent → document tree ("how it was organized")

plus refs[] (citations) and a timeline (created_at). One block can appear in
both trees at once. Phase 0 stores the structure; richer views come later.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class BlockKind(enum.StrEnum):
    message = "message"
    doc = "doc"
    # B1: a structured node inside the living doc's block tree (heading/paragraph/
    # list/code/quote). The `doc` block stays the canonical markdown; doc_node
    # blocks are its struct_parent children, carrying stable ids for downstream
    # anchoring (cross-view highlight, comments, live refs). Excluded from the
    # conversation timeline.
    doc_node = "doc_node"
    # B4: an inline comment anchored to a doc node (reply_to = the doc_node id).
    # Shown in the document margin, not the conversation timeline.
    comment = "comment"
    decision = "decision"
    attachment = "attachment"
    event = "event"
    # A renderable product 芝士 explicitly points at (spec §9.1): content = the
    # worktree-relative file path, mime_type = how to render it (text/html,
    # image/svg+xml). The latest artifact of a topic is its "current preview";
    # created via `cheese artifact`. Never inferred from prose — the AI names it.
    artifact = "artifact"


class AuthorType(enum.StrEnum):
    human = "human"
    ai = "ai"  # 芝士 (本体 or 分身)
    system = "system"


class Block(UuidPk, Timestamps, Base):
    __tablename__ = "blocks"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )

    kind: Mapped[BlockKind] = mapped_column(
        Enum(BlockKind, native_enum=False, length=16),
        default=BlockKind.message,
    )
    author_type: Mapped[AuthorType] = mapped_column(
        Enum(AuthorType, native_enum=False, length=16)
    )
    # Free-form author handle (user id, or "cheese" for the AI). Phase 0 has no
    # user table yet, so this stays a string.
    author: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text, default="")

    # Conversation tree: which block this one replies to.
    reply_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("blocks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Document tree: position in the structured document.
    struct_parent: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("blocks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # B1 doc-as-block-tree: structural node type (heading/paragraph/list/code/
    # quote) and sibling order under struct_parent. Only set on kind=doc nodes.
    node_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    struct_order: Mapped[float | None] = mapped_column(Float, nullable=True)

    # B4 段落评论: the exact text a comment was selected on (Feishu-style). The
    # comment still anchors to its paragraph via reply_to; this preserves the quoted
    # span for display. Only set on kind=comment blocks.
    anchor_quote: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Render-by-type (spec §9.1): the mimeType of an artifact block — the host
    # picks a renderer from this, never from parsing the AI's text. Only set on
    # kind=artifact blocks (e.g. text/html, image/svg+xml).
    mime_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # The agent turn that produced this block (review R4): groups a turn's blocks
    # for traceability / recovery / the collaboration-trajectory dataset. Null for
    # human-authored or pre-R4 blocks.
    turn_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    # Structured event payload (kind=event): {"tool": <name>, "arg": <preview>,
    # "platform": <bool>} so the UI translates/classifies at DISPLAY time instead
    # of relying on text baked into `content` (which stays as a human-readable
    # fallback for old clients / old rows). Null on non-event blocks and on
    # event rows created before this field existed.
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Citations: which decisions / PRs / files this block leans on.
    refs: Mapped[list[str]] = mapped_column(JSON, default=list)

    # If this block was upgraded into its own topic (spec §6.1), the original
    # position becomes a live link to the new topic.
    upgraded_to_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )


class BlockReaction(UuidPk, Base):
    """An emoji reaction on a block — Slack semantics (协作平台的消息表情).

    One row per (block, emoji, author); reacting again with the same emoji
    removes the row (toggle). Both humans and 芝士 react through this table —
    e.g. the platform's deterministic ✅ receipt on a summoning message."""

    __tablename__ = "block_reactions"
    __table_args__ = (
        UniqueConstraint("block_id", "emoji", "author", name="uq_block_reaction"),
    )

    block_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("blocks.id", ondelete="CASCADE"), index=True
    )
    emoji: Mapped[str] = mapped_column(String(32))
    # Free-form author handle, same convention as Block.author ("cheese" = AI).
    author: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
