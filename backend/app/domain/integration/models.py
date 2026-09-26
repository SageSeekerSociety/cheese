"""A person's own mailbox or Feishu account, lent to the AI teammates of the
projects they name, and the mail drafts those teammates write into it.

The secret is sealed at rest and bound to its row. Nothing is sent from a
mailbox without its owner confirming the exact draft that was written.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class Integration(UuidPk, Timestamps, Base):
    __tablename__ = "integrations"

    owner_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    owner_handle: Mapped[str] = mapped_column(String(64))
    #: mail | feishu
    provider: Mapped[str] = mapped_column(String(16))
    #: What the owner sees it as: the address, or the Feishu app's name.
    label: Mapped[str] = mapped_column(String(200))
    #: Non-secret settings (hosts, ports, username; app_id, domain, folder).
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    #: {"password": …} / {"app_secret": …, "user_access_token": …, …}, sealed.
    secret: Mapped[str] = mapped_column(Text)
    #: Project ids whose AI teammates may use it.
    grants: Mapped[list] = mapped_column(JSONB, default=list)
    #: ok | auth_failed | unreachable | error
    status: Mapped[str] = mapped_column(String(16), default="ok")
    last_error: Mapped[str] = mapped_column(Text, default="", server_default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MailDraft(UuidPk, Timestamps, Base):
    __tablename__ = "mail_drafts"

    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integrations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    topic_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64))
    to: Mapped[list] = mapped_column(JSONB, default=list)
    cc: Mapped[list] = mapped_column(JSONB, default=list)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    #: [{"path": room path, "name": …, "size": …, "sha256": …}]
    attachments: Mapped[list] = mapped_column(JSONB, default=list)
    in_reply_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The Message-ID the draft was stored under in the mailbox.
    message_id: Mapped[str] = mapped_column(Text)
    #: drafted | sent | failed | discarded
    status: Mapped[str] = mapped_column(String(16), default="drafted")
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
