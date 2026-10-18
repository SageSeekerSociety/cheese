"""A project's MCP servers added in its settings — remote ones and commands —
and its connections to the remote MCP servers it declares (there, in its
`.mcp.json`, or by a type).

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
from sqlalchemy.dialects.postgresql import JSONB
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


class ProjectMcpServer(UuidPk, Timestamps, Base):
    """A remote server added in project settings, beside those the repository's
    `.mcp.json` declares. A server that sends a header keeps the header's value
    as the project secret its template names (`declared.settings_variable`)."""

    __tablename__ = "project_mcp_servers"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_project_mcp_server"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_project_mcp_servers_project_id_projects",
        ),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text)
    #: The header it is authorized by; None is OAuth.
    header_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    added_by: Mapped[str] = mapped_column(String(64))


class ProjectMcpCommand(UuidPk, Timestamps, Base):
    """A local server added in project settings: a command, run on the room's
    machine like one the repository's `.mcp.json` names. A secret it needs is
    written as a `${VAR}` and kept as a project secret."""

    __tablename__ = "project_mcp_commands"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_project_mcp_command"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_project_mcp_commands_project_id_projects",
        ),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64))
    command: Mapped[str] = mapped_column(Text)
    args: Mapped[list] = mapped_column(JSONB, default=list)
    env: Mapped[dict] = mapped_column(JSONB, default=dict)
    added_by: Mapped[str] = mapped_column(String(64))


class ProjectMcpRun(UuidPk, Timestamps, Base):
    """How one of a project's local MCP servers — a command the repository's
    `.mcp.json`, its settings or a teammate's type names, run on the room's
    machine — last fared, as the session's gateway reported it: running,
    failed (with the end of what it printed), or waiting for the owner of the
    member's computer it would run on. `updated_at` is when it was said."""

    __tablename__ = "project_mcp_runs"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_project_mcp_run"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_project_mcp_runs_project_id_projects",
        ),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128))
    #: "running", "failed", "awaiting", "refused" or "idle".
    state: Mapped[str] = mapped_column(String(16))
    #: The last lines it printed, when it failed.
    log: Mapped[str] = mapped_column(Text, default="")
    #: The member's computer it ran on, or would; None on the platform's.
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ProjectMcpApproval(UuidPk, Timestamps, Base):
    """Whether the owner of a member's computer lets one command of the
    project's run on it. A command is told apart by its fingerprint, over the
    definition as written (`${VAR}` unexpanded), which the computer itself
    computes before it starts one: a changed command is a new question.
    ``allowed`` is None while the owner has not answered."""

    __tablename__ = "project_mcp_approvals"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "device_id", "fingerprint", name="uq_project_mcp_approval"
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_project_mcp_approvals_project_id_projects",
        ),
        index=True,
    )
    device_id: Mapped[str] = mapped_column(
        ForeignKey(
            "device.device_id",
            ondelete="CASCADE",
            name="fk_project_mcp_approvals_device_id_device",
        ),
        index=True,
    )
    #: The server's name, and the command line as written, for the owner to read.
    name: Mapped[str] = mapped_column(String(128))
    command: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    #: Where the command is written: "repository" (`.mcp.json`), "settings"
    #: or "type".
    source: Mapped[str] = mapped_column(
        String(16), default="repository", server_default="repository"
    )
    #: Who wrote it: the last to change the server in `.mcp.json`, as the
    #: repository names them, or the member who added it in settings; None when
    #: it could not be read.
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
