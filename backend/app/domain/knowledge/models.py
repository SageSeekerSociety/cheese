from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class KnowledgeType(str, Enum):
    MATERIAL = "MATERIAL"
    LINK = "LINK"
    TEXT = "TEXT"
    CODE = "CODE"


class KnowledgeSource(str, Enum):
    MANUAL = "MANUAL"
    FROM_DISCUSSION = "FROM_DISCUSSION"


knowledge_seq = Sequence("knowledge_seq")


class Knowledge(Base):
    __tablename__ = "knowledge"

    id: Mapped[int] = mapped_column(
        BigInteger,
        knowledge_seq,
        primary_key=True,
        server_default=knowledge_seq.next_value(),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    type: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)

    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    material_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int] = mapped_column("created_by_id", Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(255), nullable=False, default="MANUAL"
    )
    project_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    discussion_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


knowledge_label_seq = Sequence("knowledge_label_seq")


class KnowledgeLabel(Base):
    __tablename__ = "knowledge_label"

    id: Mapped[int] = mapped_column(
        BigInteger,
        knowledge_label_seq,
        primary_key=True,
        server_default=knowledge_label_seq.next_value(),
    )
    knowledge_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    label: Mapped[str] = mapped_column(String(length=50), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class KnowledgeUpvote(Base):
    __tablename__ = "knowledge_upvote"
    __table_args__ = (
        UniqueConstraint("knowledge_id", "user_id", name="uq_knowledge_upvote"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    knowledge_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
