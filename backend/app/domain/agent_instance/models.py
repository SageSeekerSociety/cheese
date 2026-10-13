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


class OwnCallers(enum.StrEnum):
    """Who may set a member's own agent to work. Its owner decides; the
    platform does not look at what the agent signs in with."""

    #: Its owner alone.
    owner = "owner"
    #: Any person in the project, the owner included.
    project = "project"


class OwnAgent(Base):
    """A project's agent that is one member's own coding agent (#2991): their
    own Claude Code with one of their model connections — the login they gave
    the platform on one of their machines (`cheesehost claude login`) — called
    by whoever its owner lets call it (``callers``).

    It is still an ``AgentInstance`` — a memory pool, a seat, a name in the
    roster — and this row is what makes it someone's. A person may have one per
    connection in a project; its owner adds it there and removes it.

    Its connection is either a machine's login (``device_id``) or one its
    owner's account holds (``account_connection_id``), never both.
    """

    __tablename__ = "own_agents"
    __table_args__ = (
        CheckConstraint(
            "device_id IS NULL OR account_connection_id IS NULL",
            name="ck_own_agents_one_connection",
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
    # The model connection it runs on: the login on this machine
    # (`DeviceClaudeLoginRow`, keyed by the machine). Its sessions run there and
    # nowhere else — a subscription's login never leaves the machine it was
    # given on. None once the machine is gone; then it has nothing to run on.
    device_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "device.device_id",
            ondelete="SET NULL",
            name="fk_own_agents_device_id_device",
        ),
        nullable=True,
        index=True,
    )
    # Or the model connection its owner's account holds (`AccountConnection`):
    # an API key or a model service the platform keeps sealed. Its sessions run
    # on the deployment's session host. None once its owner removed it.
    account_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "account_model_connections.id",
            ondelete="SET NULL",
            name="fk_own_agents_account_connection_id_account_model_connections",
        ),
        nullable=True,
        index=True,
    )
    callers: Mapped[OwnCallers] = mapped_column(
        Enum(
            OwnCallers,
            native_enum=False,
            length=16,
            create_constraint=True,
            name="ck_own_agents_callers",
        ),
        nullable=False,
        default=OwnCallers.owner,
        server_default=OwnCallers.owner.value,
    )


class AccountConnection(UuidPk, Timestamps, Base):
    """A model connection a person's account holds rather than one of their
    machines (#2991): an Anthropic API key, or another model service that
    speaks Anthropic's API (its address, key and model). A member's own Claude
    Code on it runs on the deployment's session host, as the project's own
    agents do, and calls the model with this key.

    A Claude subscription is never one: its login may not be held by a third
    party, so it stays on the machine it was given on (`DeviceClaudeLoginRow`),
    and the kind's CHECK keeps it out of this table.

    The key is sealed (`app.core.crypto`, `Purpose.MODEL_CONNECTION_KEY`, bound
    to this row); only its last four characters are kept readable, for its
    owner to tell keys apart.
    """

    __tablename__ = "account_model_connections"

    owner_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "user.id",
            ondelete="CASCADE",
            name="fk_account_model_connections_owner_user_id_user",
        ),
        nullable=False,
        index=True,
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
