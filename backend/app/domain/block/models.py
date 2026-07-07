"""万物皆块 — `domain/block` (§2.1).

One append-only substrate for chat messages (and, later, document nodes). A chat message
is a `block` with `thread_id` set and optionally `reply_to_id` (the conversation tree);
document nodes hang on `struct_parent_id` (the document tree). Both live here; the
distinction is `kind` + which tree they hang on. `author_id` is always a `user_id`
(human or agent — never distinguished).
"""

from datetime import datetime
from enum import IntEnum

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Sequence,
    SmallInteger,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

block_seq = Sequence("block_seq")


class BlockKind(IntEnum):
    MESSAGE = 0  # a chat message in a thread
    DOC_NODE = 1  # a document node (struct tree) — reserved for domain/document


class Block(Base):
    __tablename__ = "block"
    __table_args__ = (Index("ix_block_thread_id_id", "thread_id", "id"),)

    id: Mapped[int] = mapped_column(
        BigInteger, block_seq, primary_key=True, server_default=block_seq.next_value()
    )
    project_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("thread.id"), nullable=True)
    kind: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=BlockKind.MESSAGE)
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    reply_to_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # conversation tree
    struct_parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # document tree

    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
