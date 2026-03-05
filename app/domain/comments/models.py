import enum
from datetime import datetime

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class CommentableType(str, enum.Enum):
    QUESTION = "QUESTION"
    ANSWER = "ANSWER"
    COMMENT = "COMMENT"
    MATERIAL_BUNDLE = "MATERIAL_BUNDLE"
    KNOWLEDGE = "KNOWLEDGE"


class Base(DeclarativeBase):
    pass


CommentCommentabletypeEnum = SQLEnum(
    "ANSWER",
    "COMMENT",
    "QUESTION",
    name="CommentCommentabletypeEnum",
    create_type=False,
)


class Comment(Base):
    __tablename__ = "comment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    commentable_type: Mapped[str] = mapped_column(CommentCommentabletypeEnum, nullable=False)
    commentable_id: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
