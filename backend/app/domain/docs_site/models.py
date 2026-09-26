"""Tables for the docs site: what was asked of 问芝士, and platform-held credentials."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class DocsQuestion(UuidPk, Base):
    """One question asked of 问芝士, and how it went.

    Kept for two readers: the rate limiter's daily count has a durable record
    behind it, and whoever maintains the docs can see what people ask that the
    docs do not answer (``outcome = 'no_match'``) — the most direct signal of a
    missing page. Rows older than ``DOCS_QUESTION_RETENTION_DAYS`` are purged.
    """

    __tablename__ = "docs_questions"

    # SET NULL, not CASCADE: deleting an account removes who asked, not the
    # signal that a question went unanswered.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    question: Mapped[str] = mapped_column(Text)
    # The page the reader was on, when they chose to include it.
    page: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # answered | no_match | failed
    outcome: Mapped[str] = mapped_column(String(16))
    # The section URLs the answer was grounded in, best first.
    sources: Mapped[list] = mapped_column(JSONB, default=list)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_docs_questions_created_at", "created_at"),
        Index("ix_docs_questions_user_created", "user_id", "created_at"),
        Index("ix_docs_questions_outcome_created", "outcome", "created_at"),
    )


class ServiceCredential(Timestamps, Base):
    """A credential the platform mints for itself at run time and must keep.

    The gateway's virtual key for 问芝士 is the first: minting one is not
    idempotent on the gateway, so it is minted once, under an advisory lock, and
    stored here rather than in any deployment's env file.
    """

    __tablename__ = "service_credentials"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    secret: Mapped[str] = mapped_column(Text)
