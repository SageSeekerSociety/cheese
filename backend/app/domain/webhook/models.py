"""Webhook credential state — one row per conversation (a room or a task).

The credential itself (an HMAC token, see ``app.core.webhook_auth``) is never
stored: only a ``version`` counter is. Minting bumps the version and signs a
token embedding it; verification re-derives the signature and compares the
embedded version against the row's current value. Rotating (mint again) or
revoking (bump ``version`` without handing out the new token) both work by
moving this counter — no plaintext or hash of the live secret ever touches
the database.
"""

import uuid

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk

# The registry `conversation_id` points at: mapped wherever this is, so the
# foreign key resolves in a process that never imports `app.models`.
from app.domain.conversation.models import Conversation  # noqa: F401


class WebhookToken(UuidPk, Timestamps, Base):
    __tablename__ = "webhook_tokens"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), unique=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
