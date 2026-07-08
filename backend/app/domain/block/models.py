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
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Sequence,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

block_seq = Sequence("block_seq")


class BlockKind(IntEnum):
    MESSAGE = 0  # a chat message in a thread (conversation tree via reply_to_id)
    DOC_NODE = 1  # a document node (one top-level markdown block; document tree)
    DOC_ROOT = 2  # a document's canonical body (whole markdown); doc_node children hang off it
    EVENT = 3  # a system event on a thread/document (e.g. "edited the doc" → an instruction)


class Block(Base):
    __tablename__ = "block"
    __table_args__ = (
        Index("ix_block_thread_id_id", "thread_id", "id"),
        # Document tree lookups: a doc-root's ordered children.
        Index("ix_block_struct_parent_order", "struct_parent_id", "struct_order"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, block_seq, primary_key=True, server_default=block_seq.next_value()
    )
    project_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("thread.id"), nullable=True)
    kind: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=BlockKind.MESSAGE)
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    reply_to_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # conversation tree
    struct_parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # document tree
    # Sibling ordering within the document tree. Float so a node can be inserted
    # between two others without renumbering (index-based on save; see doc_tree).
    struct_order: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Coarse structural type of a doc node (heading/paragraph/list/code/quote).
    # Structure only — never a semantic label inferred from natural language.
    node_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Citations: ids of the decisions / PRs / files / topics this block draws on.
    refs: Mapped[list | None] = mapped_column(JSON, nullable=True)

    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Soft delete: set when a message is deleted (kept as a tombstone so replies/quotes
    # that reference it degrade gracefully instead of dangling).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Pin: set when a message is pinned in its thread (Feishu-style). Pinned messages =
    # blocks with pinned_at not null, ordered by pinned_at.
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
