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

from sqlalchemy import JSON, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class BlockKind(enum.StrEnum):
    message = "message"
    doc = "doc"
    decision = "decision"
    attachment = "attachment"
    event = "event"


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
    # Citations: which decisions / PRs / files this block leans on.
    refs: Mapped[list[str]] = mapped_column(JSON, default=list)

    # If this block was upgraded into its own topic (spec §6.1), the original
    # position becomes a live link to the new topic.
    upgraded_to_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )
