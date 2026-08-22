"""Webhook credential state — one row per place (a room's main line, or a thread).

The credential itself (an HMAC token, see ``app.core.webhook_auth``) is never
stored: only a ``version`` counter is. Minting bumps the version and signs a
token embedding it; verification re-derives the signature and compares the
embedded version against the row's current value. Rotating (mint again) or
revoking (bump ``version`` without handing out the new token) both work by
moving this counter — no plaintext or hash of the live secret ever touches
the database.
"""

import uuid

from sqlalchemy import ForeignKey, Index, Integer, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class WebhookToken(UuidPk, Timestamps, Base):
    __tablename__ = "webhook_tokens"
    # `topic_id` used to BE the primary key; a thread's row is identified by
    # (room, thread) and a primary key cannot hold the NULL that means "the
    # room's own main line". Two partial unique indexes rather than one wider
    # one, because NULL is not equal to NULL in a unique index.
    __table_args__ = (
        Index(
            "uq_webhook_tokens_room",
            "topic_id",
            unique=True,
            postgresql_where=text("task_id IS NULL"),
        ),
        Index(
            "uq_webhook_tokens_thread",
            "task_id",
            unique=True,
            postgresql_where=text("task_id IS NOT NULL"),
        ),
    )

    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
