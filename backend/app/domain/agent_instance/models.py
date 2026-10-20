"""A project agent owns its configuration and memory.

Built-in presets initialize the configuration once at creation. The stable
handle keys memory within the project; room identities still key authorship.

A project always has at least this one row: it is created with its 芝士 and a
seat for it in the project's own room (结论 4).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class NameSource(enum.StrEnum):
    """Whether an agent still carries the name the platform gave it.

    ``default`` is the name a project's first agent is born with, stored as
    「芝士」 because agents and prompts read it; each screen shows it in its
    reader's language until somebody names the agent. ``human`` is a name a
    person chose, shown as written.
    """

    default = "default"
    human = "human"


class AgentInstance(UuidPk, Timestamps, Base):
    __tablename__ = "agent_instances"
    __table_args__ = (
        UniqueConstraint("project_id", "handle", name="uq_agent_instance_handle"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The memory pool key within the project. A project is created with its 芝士
    # under the ``cheese`` handle, so every agent acting in a project — its 芝士
    # included — has a row here, and every pool has an owner.
    handle: Mapped[str] = mapped_column(String(64))
    # Which agent type backs it. NULL = 芝士 with no specialization, which is
    # what every project got before types existed.
    type_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Creation provenance only; runtime reads this agent's saved configuration.
    configuration: Mapped[dict] = mapped_column(JSON, nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    name_source: Mapped[NameSource] = mapped_column(
        Enum(NameSource, native_enum=False, length=16),
        default=NameSource.human,
        server_default=NameSource.human.value,
    )
    # Retiring an agent cannot delete this row: the memory pool is keyed by
    # ``handle`` and the rooms already working with it point at its id, so
    # dropping the row would strand both. It stays, and stops being offered.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class OwnAgent(Base):
    """A project teammate backed by a team connection.

    The member owner supplies the prompt's ownership context. Connection access
    follows the project's team, independent of that member's current membership.
    """

    __tablename__ = "own_agents"
    __table_args__ = (
        CheckConstraint(
            "login_id IS NULL OR account_connection_id IS NULL",
            name="ck_own_agents_login_connection",
        ),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_instances.id", ondelete="CASCADE"), primary_key=True
    )
    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Which harness it is: the coding agent the owner logged in on the machine.
    harness: Mapped[str] = mapped_column(String(64), nullable=False)
    # The credential device stays the model process's host; tools execute on
    # the teammate's selected execution device.
    login_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "claude_logins.id",
            ondelete="SET NULL",
            name="fk_own_agents_login_id_claude_logins",
        ),
        nullable=True,
        index=True,
    )
    # Or an encrypted team API key or model service, removed with its connection.
    account_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "account_model_connections.id",
            ondelete="SET NULL",
            name="fk_own_agents_account_connection_id_account_model_connections",
        ),
        nullable=True,
        index=True,
    )


class AccountConnection(UuidPk, Timestamps, Base):
    """An encrypted team API key or Anthropic-compatible model service.

    Subscription credentials remain on their device. The platform stores only
    a sealed key bound to this row and its last four readable characters.
    """

    __tablename__ = "account_model_connections"
    team_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "team.id",
            ondelete="CASCADE",
            name="fk_account_model_connections_team_id_team",
        ),
        nullable=False,
        index=True,
    )
    allowed_models: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: `ConnectionKind`'s ``api_key`` or ``model_service``.
    kind: Mapped[str] = mapped_column(
        Enum(
            "api_key",
            "model_service",
            native_enum=False,
            length=16,
            create_constraint=True,
            name="ck_account_model_connections_kind",
        ),
        nullable=False,
    )
    #: What its owner calls it; None until they name it.
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The service's address; None for an Anthropic API key, which calls
    #: Anthropic.
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: The model a model service is called with; None for an API key.
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    key_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    #: When the service last answered a check, and why it refused if it did.
    checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    check_error: Mapped[str | None] = mapped_column(Text, nullable=True)
