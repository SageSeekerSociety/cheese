from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, Integer, Sequence, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

discussion_seq = Sequence("discussion_seq")
discussion_reaction_seq = Sequence("discussion_reaction_seq")
reaction_type_seq = Sequence("reaction_type_seq")


class DiscussableModelType(str, Enum):
    PROJECT = "PROJECT"
    SPACE = "SPACE"
    TEAM = "TEAM"
    TASK = "TASK"
    KNOWLEDGE = "KNOWLEDGE"
    QUESTION = "QUESTION"
    ANSWER = "ANSWER"


class Discussion(Base):
    __tablename__ = "discussion"

    id: Mapped[int] = mapped_column(
        Integer, discussion_seq, primary_key=True, server_default=discussion_seq.next_value()
    )
    model_type: Mapped[str] = mapped_column(String(length=255), nullable=False)
    model_id: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sender_id: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DiscussionMentionedUser(Base):
    __tablename__ = "discussion_mentioned_users"

    discussion_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class ReactionType(Base):
    __tablename__ = "reaction_type"

    id: Mapped[int] = mapped_column(
        Integer, reaction_type_seq, primary_key=True, server_default=reaction_type_seq.next_value()
    )
    code: Mapped[str] = mapped_column(String(length=32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(length=64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(length=255), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DiscussionReaction(Base):
    __tablename__ = "discussion_reaction"

    id: Mapped[int] = mapped_column(
        Integer,
        discussion_reaction_seq,
        primary_key=True,
        server_default=discussion_reaction_seq.next_value(),
    )
    discussion_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reaction_type_id: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
