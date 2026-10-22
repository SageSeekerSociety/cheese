"""A chapter: a message someone marked as the start of a section, so a long
conversation has an outline.

A chapter belongs to one conversation — a channel's main line, a task or a
支线 — and everyone reading that conversation sees the same outline. It is not
a channel pin: a pin keeps something at the top of an overview and says so in
the main line; a chapter is navigation inside the conversation and says
nothing, so pinning one does not interrupt anyone who is reading it.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


class Chapter(UuidPk, Base):
    __tablename__ = "chapters"

    #: The conversation the chapter is a section of; a block cannot move to
    #: another conversation, so this is the message's own.
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "conversations.id",
            ondelete="CASCADE",
            name="fk_chapters_conversation_id_conversations",
        ),
        index=True,
    )
    #: The message the section starts at. One message is one chapter.
    block_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("blocks.id", ondelete="CASCADE", name="fk_chapters_block_id_blocks"),
        unique=True,
    )
    pinned_by: Mapped[str] = mapped_column(String(128))
    pinned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
