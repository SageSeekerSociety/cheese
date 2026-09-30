"""A person's own mailbox or Feishu account, lent to the AI teammates of the
projects they name, the mail drafts those teammates write into it, and the one
Feishu app the platform administrator configures for everybody.

The secret is sealed at rest and bound to its row. Nothing is sent from a
mailbox without its owner confirming the exact draft that was written.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk

#: The platform's Feishu app is a singleton; its row id is the constant below, so
#: "has an administrator configured it" is one ``get`` and a second row cannot
#: appear by accident.
FEISHU_APP_ROW_ID = 1


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


class FeishuApp(Timestamps, Base):
    """The Feishu app the platform administrator configures once for everybody.

    Members used to create a custom app each and paste its App ID and Secret into
    «我的连接». Now one app belongs to the platform: an administrator fills it in
    on the admin page, a member clicks once to authorize, and the member's own
    row (``Integration``) keeps only that person's ``user_access_token`` and
    ``refresh_token``. Which app credentials a call uses is read from here at
    the moment of the call, so rotating the secret reaches every connection.

    ``app_id`` is not a secret; ``app_secret`` is sealed exactly like a
    connection's secret (``Purpose.INTEGRATION_SECRET``, bound to this row).
    """

    __tablename__ = "feishu_apps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(64))
    #: feishu | lark
    domain: Mapped[str] = mapped_column(String(16), default="feishu")
    #: {"app_secret": …}, sealed.
    secret: Mapped[str] = mapped_column(Text)
    #: The platform administrator who last saved it.
    updated_by: Mapped[str] = mapped_column(String(64))
