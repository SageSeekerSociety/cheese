"""Agent: a project-owned AI actor (the registry of "which agents exist").

Hard invariant (see architecture doc §2): every agent belongs to exactly one
``project_id`` — there is no free-floating agent, and no space/task-owned
agent. A project may own many agents; a 分身 (clone) points at its 本体 via
``parent_agent_id`` and shares the same project.

``status`` here is a **lifecycle/control** state owned by the orchestrator
(active/paused/stopped) — deliberately NOT busy/idle. Whether an agent is
busy is *derived* from whether it holds an in-progress work item, never a
self-reported flag (which would drift).

Power/responsibility lives in ``role_prompt`` (soft, prompt-conveyed), not in
permissions: every agent holds the full project's permissions. ``adapter_kind``
selects the execution path (via a machine, or a direct LLM call).
"""

from datetime import datetime
from enum import IntEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Sequence, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

agent_seq = Sequence("agent_seq")


class AdapterKind(IntEnum):
    """How the agent executes."""

    CHEESED_CODEX = 0  # interactive Claude Code on a machine, via the connector
    NAIVE_API = 1  # direct LLM calls, no machine


class AgentStatus(IntEnum):
    """Orchestrator-owned lifecycle state (NOT busy/idle — that is derived)."""

    ACTIVE = 0
    PAUSED = 1
    STOPPED = 2


class Agent(Base):
    __tablename__ = "agent"

    id: Mapped[int] = mapped_column(
        BigInteger, agent_seq, primary_key=True, server_default=agent_seq.next_value()
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    adapter_kind: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=AdapterKind.CHEESED_CODEX.value
    )
    status: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=AgentStatus.ACTIVE.value
    )
    # A 分身 (clone) references its 本体; primaries have NULL.
    parent_agent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("agent.id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
