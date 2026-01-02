from __future__ import annotations

from datetime import datetime
from typing import List

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, Sequence, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AIUserQuota(Base):
    __tablename__ = "user_ai_quota"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Sequence("user_ai_quota_seq", start=1, increment=50),
        primary_key=True,
    )
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    daily_seu_quota: Mapped[float | None] = mapped_column(Float, nullable=True)
    remaining_seu: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_seu_consumed: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_reset_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AIConversation(Base):
    __tablename__ = "ai_conversation"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Sequence("ai_conversation_seq", start=1, increment=50),
        primary_key=True,
    )
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    context_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_type: Mapped[str] = mapped_column(Text, nullable=False, default="standard")
    module_type: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    messages: Mapped[List["AIMessage"]] = relationship(
        "AIMessage", back_populates="conversation", lazy="selectin"
    )


class AIMessage(Base):
    __tablename__ = "ai_message"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Sequence("ai_message_seq", start=1, increment=50),
        primary_key=True,
    )
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ai_conversation.id"), nullable=False, index=True
    )
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    role: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model_type: Mapped[str] = mapped_column(Text, nullable=False, default="standard")
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seu_consumed: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    conversation: Mapped["AIConversation"] = relationship(
        "AIConversation", back_populates="messages"
    )
