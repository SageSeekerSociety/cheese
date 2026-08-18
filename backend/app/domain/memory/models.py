"""Memory model.

Per spec §8.4, the block tree (DB) is the source of truth; memory is a
projection for fast AI recall, rebuildable from blocks. Phase 0 stores memory
as plain entries scoped to a project or a user. The MemoryStore abstraction
(store.py) lets us swap in OpenViking later without touching callers.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, Uuid
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
    """One pass of 记忆整理 — 芝士 rereading a topic's pools before its sandbox
    is destroyed, merging duplicates and retiring what the work disproved.

    The row is created when the pass is STARTED, not when it lands, and it stays
    behind whether or not the pass produced anything (`applied`). Both halves
    matter: without a row for the attempt, a pass that crashed or ran out of
    budget looks exactly like a topic that has never been organized, and the
    reaper starts another one every hour forever. Without `turn_id`, the blocks
    the pass itself writes are indistinguishable from someone returning to the
    topic — and since blocks are what idleness is judged on, the box would keep
    renewing its own lease off its own housekeeping.
    """

    __tablename__ = "memory_dreams"

    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The kickoff turn that ran (or is running) the pass. Null when a pass was
    # applied without the reaper having opened a row for it first.
    turn_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # What the proposal was computed against: any entry touched since then has
    # moved under 芝士's feet and is left alone. Null until the pass lands.
    snapshot_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    # 人话 summary of what changed, in 芝士's own words.
    summary: Mapped[str] = mapped_column(Text, default="")


class MemoryEntry(UuidPk, Timestamps, Base):
    __tablename__ = "memory_entries"

    scope: Mapped[MemoryScope] = mapped_column(
        Enum(MemoryScope, native_enum=False, length=16), index=True
    )
    # Project id or user handle, depending on scope.
    scope_id: Mapped[str] = mapped_column(String(128), index=True)
    content: Mapped[str] = mapped_column(Text)

    # 记忆整理 never deletes. A retired entry is invisible to recall/count/search
    # but still on disk, so a pass that merged two facts wrongly is one UPDATE
    # away from being undone — which is the whole reason 芝士 is allowed to
    # reorganize memory unattended at all.
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Which pass retired it / created it. The pair is what makes an undo
    # possible: put back everything that pass retired, retire everything it
    # added. One column could not express the second half.
    retired_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_dreams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_dreams.id", ondelete="SET NULL"), nullable=True, index=True
    )
