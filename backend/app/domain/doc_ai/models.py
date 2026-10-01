"""Database work survives request handlers and process-local wakeups."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


class DocAiRequest(UuidPk, Base):
    __tablename__ = "doc_ai_requests"
    __table_args__ = (
        CheckConstraint("kind IN ('ask', 'propose')", name="ck_doc_ai_kind"),
        CheckConstraint(
            "state IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_doc_ai_state",
        ),
        CheckConstraint("generation >= 0", name="ck_doc_ai_generation"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("blocks.id", ondelete="CASCADE")
    )
    actor: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    question: Mapped[str] = mapped_column(Text)
    base_version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(String(64))
    selection: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # A frozen binding, not a key or a writable agent-session identity.
    binding: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Retained periodic project-ledger reconciliation; no completion replay.
    meter_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DocAiAttempt(UuidPk, Base):
    __tablename__ = "doc_ai_attempts"
    __table_args__ = (
        UniqueConstraint("request_id", "generation", name="uq_doc_ai_attempt"),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("doc_ai_requests.id", ondelete="CASCADE")
    )
    generation: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Late attempts still retain their actual spend; a stale result is not free.
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DocAiProposal(UuidPk, Base):
    __tablename__ = "doc_ai_proposals"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_doc_ai_proposal_request"),
        CheckConstraint(
            "state IN ('pending', 'accepted', 'withdrawn')",
            name="ck_doc_ai_proposal_state",
        ),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("doc_ai_requests.id", ondelete="CASCADE")
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    replacement: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    accepted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    accepted_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
