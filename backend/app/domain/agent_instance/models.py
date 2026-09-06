"""Agent instance — one agent, inside one project, with its own memory.

The三层 split this table completes: a **type** is 出厂设置 and belongs to no
project; an **instance** is that type working *here*, and it owns what it has
learned here; a **session** is where one conversation got to and may be thrown
away. Memory hangs off this row because that is the only one of the three whose
lifetime matches it — a type is shared by projects that must not read each
other's notes, and a session dies every time the work moves rooms.

``handle`` is what the memory pool is keyed by inside the project
(``agent_project`` scope, ``{project}:{handle}``). It is deliberately NOT a user
row: authorship stays with the per-topic 分身 identity (``cheese-<topic hex>``),
which answers "who took this action", while this answers "whose memory is this".
Collapsing the two would tie memory back to the topic — the thing this change
exists to undo.
"""

import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class AgentInstance(UuidPk, Timestamps, Base):
    __tablename__ = "agent_instances"
    __table_args__ = (
        UniqueConstraint("project_id", "handle", name="uq_agent_instance_handle"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The memory pool key within the project. A project's implicit default agent
    # uses ``cheese`` without a row here at all, so a project that never
    # configured anything keeps writing to — and reading — the pool it already
    # had.
    handle: Mapped[str] = mapped_column(String(64))
    # Which agent type backs it. NULL = 芝士 with no specialization, which is
    # what every project got before types existed.
    type_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    # Retiring an agent cannot delete this row: the memory pool is keyed by
    # ``handle`` and the rooms already working with it point at its id, so
    # dropping the row would strand both. It stays, and stops being offered.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
