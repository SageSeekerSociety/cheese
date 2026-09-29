"""Tables for the docs site: what was asked of 问芝士, who came, and credentials."""

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
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


class DocsVisit(UuidPk, Base):
    """One visitor's presence in the docs on one UTC day — at most one row each.

    The docs site needs this to answer 「有多少人来了」, which nothing else
    records: a static page load leaves no trace on the server. The row is
    deliberately thin (no IP address, no user agent, no per-page history) and is
    written by one beacon the page fires on load; ``visits.record`` dedupes on
    ``(day, visitor_id)``, so the table grows at most one row per visitor per
    day. Purged with the questions, on the same retention window.

    ``visitor_id`` is the identity the dedupe keys on, and it is one string so
    that the two kinds of visitor can share one key: ``u:<user_id>`` for a
    signed-in reader, ``v:<random>`` for one who is not (the random value lives
    in the browser's localStorage; it is not derived from anything the visitor
    carries). ``user_id`` is kept beside it as a real column because 「其中登录
    用户有多少」 is a question a report asks — and it is SET NULL, not CASCADE,
    for the same reason it is on ``DocsQuestion``.
    """

    __tablename__ = "docs_visits"

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    visitor_id: Mapped[str] = mapped_column(String(48))
    # The UTC day this row accounts for. A real column rather than something
    # derived from created_at: the dedupe key has to be a stored, indexable
    # value, and date_trunc() in a unique index would depend on the session's
    # time zone.
    day: Mapped[date] = mapped_column(Date)
    # The public page the visitor was on at their first visit of the day, when
    # the beacon could name it (developer pages are not public).
    page: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        # The dedupe is the table's whole design: one row per visitor per day.
        # Enforced here (not just in the insert path) so a second writer — a
        # retry, a race, a future caller — cannot double-count.
        UniqueConstraint("day", "visitor_id", name="uq_docs_visits_day_visitor"),
        Index("ix_docs_visits_day", "day"),
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
