"""A person's conversations with their 芝士 outside any project (#2285).

There is one 芝士 per person and many conversations; each belongs to the place
it was started, named by ``place_kind`` and ``place_id`` — a task today, other
places later. A conversation is visible to its owner only.

Two records of the same conversation are kept, because they answer different
questions once history is compacted:

* ``AssistantMessage`` rows are what the person said and was told, in order,
  and never shrink: the panel renders them.
* ``AssistantConversation.history`` is what the model is sent: the Pydantic AI
  message list (tool calls and their results included), cut back to the recent
  turns once it grows past the cap, with what was cut summarised in
  ``summary``.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class AssistantConversation(UuidPk, Timestamps, Base):
    __tablename__ = "assistant_conversations"
    __table_args__ = (
        # "This person's conversations here, latest first": the panel's list and
        # the conversation a place opens on.
        Index(
            "ix_assistant_conversations_owner_place",
            "user_id",
            "place_kind",
            "place_id",
            "last_active_at",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    place_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    place_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # The first question, cut short; empty until one is asked.
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    history: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")


class AssistantMessage(UuidPk, Timestamps, Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (
        Index("ix_assistant_messages_conversation_seq", "conversation_id", "seq"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
