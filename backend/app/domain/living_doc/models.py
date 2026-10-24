"""Documents: what they say now, their collaborative state, their history,
their write receipts and the comments written on them.

A document belongs to a team — a person's own documents to their personal team
— and to at most one of that team's projects (#3774): one with no project is
read and written by the team's members. A task's living document and the project's
overview are documents of the project that the task (``tasks.document_id``)
and the project (``projects.overview_document_id``) point at. One 芝士 writes
in a conversation belongs to that conversation's channel or private chat
(``room_id``) until someone saves it to the library (``kept_at``, #3828).
Every other table here hangs off ``documents.id``.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


class Document(UuidPk, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_documents_project_number"),
    )

    #: The team it belongs to; a person's own documents are their personal team's.
    team_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("team.id", ondelete="CASCADE", name="fk_documents_team_id_team"),
        index=True,
    )
    #: The project of that team it belongs to; None for a document of the team's
    #: (or the person's) own.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    #: What kind of document: only "doc" (Markdown and blocks) so far.
    kind: Mapped[str] = mapped_column(String(16), default="doc", server_default="doc")
    #: Its number among the project's documents, for addresses
    #: (`project_counters`). Only a document of the project's own has one: a
    #: task's goes by the task, the project's goals by the project.
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: None for a task's document, which goes by the task's title.
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: The Markdown exported from the collaborative state at its last store.
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: 0 until the first version is recorded.
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: Who made the latest version.
    author: Mapped[str] = mapped_column(String(128), default="system")
    #: The person the document is theirs: who made it, or whose message had
    #: 芝士 write it. It stays when others edit it, as ``author`` does not. None
    #: when no person did: the platform made it (a task's, the project's goals),
    #: or another agent or the platform asked 芝士 for it.
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: The AI teammate that wrote it when it was made, beside ``created_by``:
    #: the two are its authors, and later edits change neither. None when a
    #: person made it.
    drafted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: The channel or private chat 芝士 wrote it in. Until it is saved to the
    #: library only the people who can read that conversation can read it, and
    #: it goes when the conversation does. None for one made in the library.
    room_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE", name="fk_documents_room_id_topics"),
        nullable=True,
        index=True,
    )
    #: When someone saved it to the library; from then on it is the project's.
    kept_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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


class CommentOperation(UuidPk, Base):
    """A write to a room file's comment threads, remembered by operation id so
    that a retry after an unknown outcome replays its receipt instead of
    applying the write twice (``DocumentOperation`` does this for documents)."""

    __tablename__ = "comment_operations"
    __table_args__ = (
        UniqueConstraint(
            "room_id",
            "room_path",
            "actor",
            "action",
            "operation_id",
            name="uq_comment_operation",
        ),
        Index("ix_comment_operations_room_path", "room_id", "room_path"),
    )

    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "topics.id",
            ondelete="CASCADE",
            name="fk_comment_operations_room_id_topics",
        )
    )
    room_path: Mapped[str] = mapped_column(String(512))
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(32))
    operation_id: Mapped[uuid.UUID] = mapped_column()
    fingerprint: Mapped[str] = mapped_column(String(64))
    receipt: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DocumentComment(UuidPk, Base):
    """A comment on a document or on a room file: one that opens a thread
    (``thread_id`` None), or a reply in one.

    A thread on a document marks the words it is about in the shared document
    itself (``commentAnchor``, carrying the opening comment's id); the opening
    comment keeps the quoted words for display. A room file has no shared text
    to carry a mark, so the opening comment keeps where it points in
    ``anchor`` — the place in it (a page, a cell, a passage, an element) and
    the version it was read at."""

    __tablename__ = "document_comments"
    __table_args__ = (
        UniqueConstraint("thread_id", "sequence", name="uq_document_comment_sequence"),
        CheckConstraint(
            "(thread_id IS NULL) = (sequence IS NULL)",
            name="ck_document_comment_reply_sequence",
        ),
        # A thread hangs on exactly one subject: a document, or a file of a room.
        CheckConstraint(
            "(document_id IS NULL) <> (room_id IS NULL)",
            name="ck_document_comment_subject",
        ),
        CheckConstraint(
            "room_id IS NULL OR room_path IS NOT NULL",
            name="ck_document_comment_file_path",
        ),
        Index("ix_document_comments_room_path", "room_id", "room_path"),
    )

    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=True
    )
    #: A file's thread hangs on the room holding it; ``room_path`` is its path
    #: in that room, the same address the preview and `cheese show` use.
    room_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "topics.id",
            ondelete="CASCADE",
            name="fk_document_comments_room_id_topics",
        ),
        nullable=True,
    )
    room_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: Where a file's thread points, in the shapes a room message quotes
    #: (`block/message_input`); ``anchor_quote`` is how it reads.
    anchor: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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


class DocumentMention(Base):
    """A person a document's text @-mentioned and told so: the first time a
    person's save added ``<@handle>`` to it, while ``handle`` could open it.

    One row per document and person, whoever wrote the mention and however
    often it is removed and written again: the person was told once and is
    not told again by this document. The row is also what the document's
    readers go by to draw that person's name as a mention that reached them
    rather than as a plain reference."""

    __tablename__ = "document_mentions"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="CASCADE",
            name="fk_document_mentions_document_id_documents",
        ),
        primary_key=True,
    )
    handle: Mapped[str] = mapped_column(String(128), primary_key=True)
    #: Whose save told them.
    notified_by: Mapped[str] = mapped_column(String(128))
    notified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DocumentLink(Base):
    """One document's text referencing another of the same project
    (``<#id>``), as of its last stored version (#3827). Kept so a document can
    list what links to it; rewritten on every store (``links.sync``)."""

    __tablename__ = "document_links"

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="CASCADE",
            name="fk_document_links_source_id_documents",
        ),
        primary_key=True,
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="CASCADE",
            name="fk_document_links_target_id_documents",
        ),
        primary_key=True,
        index=True,
    )


class DocumentShare(Base):
    """A document's read-only link for people outside it (#3774): whoever holds
    the token reads its current version, without signing in. One per document;
    taking it back deletes the row, and sharing again makes a new token."""

    __tablename__ = "document_shares"
    __table_args__ = (UniqueConstraint("token", name="uq_document_shares_token"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="CASCADE",
            name="fk_document_shares_document_id_documents",
        ),
        primary_key=True,
    )
    token: Mapped[str] = mapped_column(String(64))
    #: Who made the link.
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
