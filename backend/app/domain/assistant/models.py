"""A person's conversations with their 芝士 outside any project (#2285).

There is one 芝士 per person and many conversations; each belongs to the place
it was started, named by ``place_kind`` and ``place_id`` — a task today, other
places later. A conversation is visible to its owner only.

What was said is kept as ``AssistantMessage`` rows, in order; the panel shows
them. The model's own copy of the conversation is its session's, on the session
host (``agent.personal.session``).
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


class AssistantGatewayKey(Timestamps, Base):
    """A person's own virtual key on the gateway, and how far its spend has been
    charged to them.

    Every model call a person's 芝士 makes is made on this key, so what the
    gateway records under it is exactly what that person asked for, priced the
    way the gateway prices it (cache reads at the cache rate). ``usage_ckpt`` is
    the cumulative spend already charged, in the shape a project's checkpoint
    has (``agent.gateway.drain_new_usage``)."""

    __tablename__ = "assistant_gateway_keys"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    # The one model the key may call (``keys.person_key``).
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    usage_ckpt: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


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
