"""Memory model.

Per spec §8.4, the block tree (DB) is the source of truth; memory is a
projection for fast AI recall, rebuildable from blocks. Memory is stored as
plain entries scoped to a project or a user.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class MemoryScope(enum.StrEnum):
    project = "project"  # 项目记忆: 章程/决策/进展 (spec §8.4)
    user = "user"  # 个人记忆: 跨项目, 跟着人走
    skill = "skill"  # 技能记忆: 领域知识/文档模板/场景包配置
    # 芝士 记忆: 一个 agent 在一个项目里学到的东西。People working on a project
    # each remember their own things; agents do too. Keyed per agent so a
    # project hosting several 芝士 doesn't pool one's operational trivia with
    # another's product decisions — and, like everything else about an agent,
    # it stays inside the project it was learned in.
    agent_project = "agent_project"


class MemoryLayer(enum.StrEnum):
    """Which layer a fact belongs to — i.e. when it is allowed to cost prompt.

    A pool grows without bound while the prompt does not, so "everything I
    learned" cannot be the injection unit forever: past a few dozen facts the
    ones that decide *how this agent behaves at all* start losing their seat to
    whatever happened to be written last. Splitting the pool is what stops that:

    - ``core`` — who this agent is, its standing rules and goals. Small,
      hand-curated, injected in full every single turn, never filtered by
      relevance. If it is only true sometimes, it is not core.
    - ``fact`` — everything else it learned. Retrieved against the turn's own
      context, so a pool can keep growing without any one turn paying for all
      of it. What a turn does not retrieve is still reachable through
      ``cheese recall``.
    """

    core = "core"
    fact = "fact"


def agent_project_scope_prefix(project_id: str | uuid.UUID) -> str:
    """The ``scope_id`` prefix shared by every agent pool of one project.

    Listing "what did the 芝士 in this project remember" is a prefix scan over
    this, which is why the composite key's shape lives here rather than being
    re-spelled at each call site.
    """
    return f"{project_id}:"


def agent_project_scope_id(project_id: str | uuid.UUID, agent_handle: str) -> str:
    """scope_id for :attr:`MemoryScope.agent_project`.

    ``scope_id`` is a plain 128-char string shared by every scope, so the two
    parts are joined rather than given columns of their own. A handle cannot
    contain ``:`` (it is a username), so the split is unambiguous.
    """
    return f"{agent_project_scope_prefix(project_id)}{agent_handle}"


class MemoryDream(UuidPk, Timestamps, Base):
    """A 记忆整理 pass that ran while the clock-driven organizer existed.

    Nothing writes these rows any more. They are kept because `MemoryEntry`
    points at them: each retired entry names the pass that retired it, and
    reading the pool's history means being able to follow that pointer.
    """

    __tablename__ = "memory_dreams"

    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The kickoff turn that ran the pass.
    turn_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # What the proposal was computed against.
    snapshot_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    # 人话 summary of what changed, in 芝士's own words.
    summary: Mapped[str] = mapped_column(Text, default="")


class MemoryEntry(UuidPk, Timestamps, Base):
    __tablename__ = "memory_entries"

    # Injection reads one layer of one pool at a time; the standalone
    # scope/scope_id indexes cannot answer that without a heap scan.
    __table_args__ = (
        Index("ix_memory_entries_pool_layer", "scope", "scope_id", "layer"),
    )

    scope: Mapped[MemoryScope] = mapped_column(
        Enum(MemoryScope, native_enum=False, length=16), index=True
    )
    # Project id or user handle, depending on scope.
    scope_id: Mapped[str] = mapped_column(String(128), index=True)
    content: Mapped[str] = mapped_column(Text)
    # Default `fact`: a memory earns its permanent seat, it is not born with
    # one. Anything written without saying otherwise is something learned.
    layer: Mapped[MemoryLayer] = mapped_column(
        Enum(MemoryLayer, native_enum=False, length=8),
        default=MemoryLayer.fact,
        server_default=MemoryLayer.fact.value,
    )

    # 记忆整理 retired instead of deleting, so the rows it decided against are
    # still here and still have to stay out of every read (`live_entries`).
    # Reinstating them would hand back facts 芝士 checked against the code and
    # found no longer true.
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Which pass retired it / created it.
    retired_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_dreams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_dreams.id", ondelete="SET NULL"), nullable=True, index=True
    )
