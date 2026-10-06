"""Documents: what they say now, their collaborative state, their history,
their write receipts and the comments written on them.

A document belongs to a project. A task's living document and the project's
overview are documents of the project that the task (``tasks.document_id``)
and the project (``projects.overview_document_id``) point at. Every other
table here hangs off ``documents.id``.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


class Document(UuidPk, Base):
    __tablename__ = "documents"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    #: What kind of document: only "doc" (Markdown and blocks) so far.
    kind: Mapped[str] = mapped_column(String(16), default="doc", server_default="doc")
    #: None for a task's document, which goes by the task's title.
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: The Markdown exported from the collaborative state at its last store.
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: 0 until the first version is recorded.
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: Who made the latest version.
    author: Mapped[str] = mapped_column(String(128), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class DocumentNode(UuidPk, Base):
    """One top-level block of a document's Markdown (a heading, a paragraph, a
    list…), in order. Derived from ``Document.content`` at every store, but a
    node whose text did not change keeps its id and its author: search hits,
    the paragraph a hit points at, and who wrote which passage
    (``app.api.doc_edits``) all read them."""

    __tablename__ = "document_nodes"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    node_type: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    position: Mapped[float] = mapped_column(Float)
    #: Who last changed this block's text.
    author: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DocumentLock(Base):
    """A durable per-document mutex: whoever writes the document, or its
    comment threads, holds this row first."""

    __tablename__ = "document_locks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )


class DocumentVersion(UuidPk, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_version"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    previous_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor: Mapped[str] = mapped_column(String(128))
    #: The person a change was made for, when someone else (the room's agent)
    #: made it at their request.
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DocumentState(Base):
    """The document's collaborative (Yjs) state, as the collaboration service
    last stored it. ``Document.content`` is the Markdown exported from this
    same state."""

    __tablename__ = "document_states"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[bytes] = mapped_column(LargeBinary)
    #: The suggestions pending in this state: ``{id, author, old, new,
    #: reason}``, as the service reported them (``reason`` kept from the edit
    #: that proposed each one).
    suggestions: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DocumentOperation(UuidPk, Base):
    __tablename__ = "document_operations"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "actor",
            "action",
            "operation_id",
            name="uq_document_operation",
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(32))
    operation_id: Mapped[uuid.UUID] = mapped_column()
    fingerprint: Mapped[str] = mapped_column(String(64))
    receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DocumentComment(UuidPk, Base):
    """A comment on a document: one that opens a thread (``thread_id`` None),
    or a reply in one. Which words a thread is about is marked in the shared
    document itself (``commentAnchor``, carrying the opening comment's id); the
    opening comment keeps the quoted words for display."""

    __tablename__ = "document_comments"
    __table_args__ = (
        UniqueConstraint("thread_id", "sequence", name="uq_document_comment_sequence"),
        CheckConstraint(
            "(thread_id IS NULL) = (sequence IS NULL)",
            name="ck_document_comment_reply_sequence",
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    #: The comment that opened the thread this one replies in.
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_comments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    #: A reply's place in its thread, from 1.
    sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    author: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text)
    #: The words an opening comment is about, as they read when it was written.
    anchor_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DocumentThread(Base):
    """The state of the thread a comment opened. ``revision`` moves with every
    reply, resolve and reopen, so a writer that read an older one is refused."""

    __tablename__ = "document_threads"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_document_thread_revision"),
        CheckConstraint("reply_count >= 0", name="ck_document_thread_reply_count"),
        CheckConstraint(
            "state IN ('open', 'resolved')", name="ck_document_thread_state"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_comments.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    state: Mapped[str] = mapped_column(
        String(16), default="open", server_default="open"
    )
    reply_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
