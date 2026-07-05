"""Block: the append-only substrate of the 2.0 data model ("everything is a block").

A block is one message / doc fragment / decision / attachment. Blocks are
organised by two self-referential trees plus cross-references:

- ``reply_to_id``     — the conversation tree (who replied to whom).
- ``struct_parent_id``— the document tree (structural containment).
- ``BlockRef``        — typed cross-references to any target (polymorphic).

Every block is anchored to a ``project_id`` (the aggregate root) and, when it
lives inside a conversation, a ``thread_id``. Blocks are append-only: an "edit"
is a new block referencing the old one; ``deleted_at`` is only a tombstone.
"""

from datetime import datetime
from enum import IntEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Sequence, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

block_seq = Sequence("block_seq")
block_ref_seq = Sequence("block_ref_seq")


class BlockKind(IntEnum):
    """What a block represents. Stored as SmallInteger (see CLAUDE.md enum rule)."""

    MESSAGE = 0
    DOCUMENT = 1
    DECISION = 2
    ATTACHMENT = 3


class AuthorKind(IntEnum):
    """Who authored a block. Agents are first-class authors, not just users."""

    USER = 0
    AGENT = 1


class Block(Base):
    __tablename__ = "block"

    id: Mapped[int] = mapped_column(
        BigInteger, block_seq, primary_key=True, server_default=block_seq.next_value()
    )
    # Aggregate-root anchor (cross-domain -> plain id, no ForeignKey per repo convention).
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # Owning conversation thread, when the block lives in one (nullable).
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    kind: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=BlockKind.MESSAGE.value
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_kind: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=AuthorKind.USER.value
    )

    # Dual tree — self-referential, intra-domain -> ForeignKey("block.id").
    reply_to_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("block.id"), nullable=True, index=True
    )
    struct_parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("block.id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BlockRef(Base):
    """A typed, directed reference from a block to any target (polymorphic).

    ``to_target_type`` names the target domain (e.g. "block", "agenda",
    "document"); ``rel`` names the relationship (e.g. "mentions",
    "derived_from", "answers"). Cross-domain targets stay untyped ids.
    """

    __tablename__ = "block_ref"

    id: Mapped[int] = mapped_column(
        BigInteger, block_ref_seq, primary_key=True, server_default=block_ref_seq.next_value()
    )
    from_block_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("block.id"), nullable=False, index=True
    )
    to_target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    to_target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rel: Mapped[str | None] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
