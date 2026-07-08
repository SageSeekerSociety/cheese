"""消息表情回应 (message reactions) — `domain/reaction`.

A reaction is a (block, user, emoji) triple: one row per distinct emoji a user put on
a message. Uniqueness ``(block_id, user_id, emoji)`` makes react/unreact idempotent.
Kept as its own small table (not a block) — a reaction is metadata on a message, not
content in the append-only substrate.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Sequence, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

reaction_seq = Sequence("message_reaction_seq")


class MessageReaction(Base):
    __tablename__ = "message_reaction"
    __table_args__ = (
        UniqueConstraint("block_id", "user_id", "emoji", name="uq_reaction_block_user_emoji"),
        Index("ix_reaction_block_id", "block_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, reaction_seq, primary_key=True, server_default=reaction_seq.next_value()
    )
    block_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    emoji: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
