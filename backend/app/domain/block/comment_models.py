"""Room document threads extend comment blocks, not community comments."""

import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DocCommentThread(Base):
    __tablename__ = "doc_comment_threads"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_doc_comment_revision"),
        CheckConstraint("reply_count >= 0", name="ck_doc_comment_reply_count"),
        CheckConstraint("state IN ('open', 'resolved')", name="ck_doc_comment_state"),
    )

    comment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("blocks.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    state: Mapped[str] = mapped_column(
        String(16), default="open", server_default="open"
    )
    reply_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class DocCommentReply(Base):
    __tablename__ = "doc_comment_replies"
    __table_args__ = (
        UniqueConstraint("comment_id", "sequence", name="uq_doc_comment_sequence"),
        CheckConstraint("sequence >= 1", name="ck_doc_comment_sequence"),
    )

    block_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("blocks.id", ondelete="CASCADE"), primary_key=True
    )
    comment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("doc_comment_threads.comment_id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
