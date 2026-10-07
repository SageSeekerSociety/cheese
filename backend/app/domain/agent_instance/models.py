"""A project agent owns its configuration and memory.

Built-in presets initialize the configuration once at creation. The stable
handle keys memory within the project; room identities still key authorship.

A project always has at least this one row: it is created with its 芝士 and a
seat for it in the project's own room (结论 4).
"""

import enum
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Enum,
    ForeignKey,
    Integer,
    String,
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


class OwnAgent(Base):
    """A project's agent that is one member's own coding agent (#2991): their
    own Claude Code, run on their own machine with the login they gave the
    platform (`cheesehost claude login`), and called by them alone.

    It is still an ``AgentInstance`` — a memory pool, a seat, a name in the
    roster — and this row is what makes it someone's. The owner's machine is
    not recorded here: a session goes to whichever of their machines is online
    with that login when it starts.
    """

    __tablename__ = "own_agents"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_instances.id", ondelete="CASCADE"), primary_key=True
    )
    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Which harness it is: the coding agent the owner logged in on the machine.
    harness: Mapped[str] = mapped_column(String(64), nullable=False)
