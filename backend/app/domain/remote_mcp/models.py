"""A project's connections to the remote MCP servers its `.mcp.json` declares.

Both tables belong to the PROJECT, never to a person: whoever connects a server
connects it for every session in the project, and is recorded so everyone can
see whose upstream account the sessions act as. Tokens and secret values are
sealed at rest and bound to their row; no route returns them.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class ProjectMcpConnection(UuidPk, Timestamps, Base):
    __tablename__ = "project_mcp_connections"
    __table_args__ = (UniqueConstraint("project_id", "server_name"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    server_name: Mapped[str] = mapped_column(String(128))
    #: The URL the token was issued for. The proxy sends the token here and
    #: nowhere else; a `.mcp.json` that now names another URL needs a reconnect.
    server_url: Mapped[str] = mapped_column(Text)
    #: The canonical resource URI sent as `resource` (RFC 8707).
    resource: Mapped[str] = mapped_column(Text)
    issuer: Mapped[str] = mapped_column(Text)
    token_endpoint: Mapped[str] = mapped_column(Text)
    revocation_endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_id: Mapped[str] = mapped_column(Text)
    #: Sealed; only a registration that issued one has it.
    client_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: none | client_secret_basic | client_secret_post
    token_endpoint_auth_method: Mapped[str] = mapped_column(String(32))
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scope: Mapped[str] = mapped_column(Text, default="", server_default="")
    authorized_by: Mapped[str] = mapped_column(String(64))
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: The authorization server refused a refresh: someone has to connect again.
    needs_reconnect: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )


class ProjectMcpSecret(UuidPk, Timestamps, Base):
    """A value for one `${VAR}` a remote server's `.mcp.json` entry references."""

    __tablename__ = "project_mcp_secrets"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    value: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[str] = mapped_column(String(64))
