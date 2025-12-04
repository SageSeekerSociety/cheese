from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import Integer, String, Text, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class KnowledgeType(str, Enum):
    MATERIAL = "MATERIAL"
    LINK = "LINK"
    TEXT = "TEXT"
    CODE = "CODE"


class KnowledgeSource(str, Enum):
    MANUAL = "MANUAL"
    FROM_DISCUSSION = "FROM_DISCUSSION"


class Knowledge(Base):
    __tablename__ = "knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(length=32), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)

    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    material_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(length=32), nullable=False, default="MANUAL")
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discussion_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class KnowledgeLabel(Base):
    __tablename__ = "knowledge_label"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_id: Mapped[int] = mapped_column(Integer, ForeignKey("knowledge.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(length=50), nullable=False)

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class KnowledgeUpvote(Base):
    __tablename__ = "knowledge_upvote"
    __table_args__ = (
        UniqueConstraint("knowledge_id", "user_id", name="uq_knowledge_upvote"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_id: Mapped[int] = mapped_column(Integer, ForeignKey("knowledge.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
