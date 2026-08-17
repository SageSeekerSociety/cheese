"""Memory model.

Per spec §8.4, the block tree (DB) is the source of truth; memory is a
projection for fast AI recall, rebuildable from blocks. Phase 0 stores memory
as plain entries scoped to a project or a user. The MemoryStore abstraction
(store.py) lets us swap in OpenViking later without touching callers.
"""

import enum
import uuid

from sqlalchemy import Enum, String, Text
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


class MemoryEntry(UuidPk, Timestamps, Base):
    __tablename__ = "memory_entries"

    scope: Mapped[MemoryScope] = mapped_column(
        Enum(MemoryScope, native_enum=False, length=16), index=True
    )
    # Project id or user handle, depending on scope.
    scope_id: Mapped[str] = mapped_column(String(128), index=True)
    content: Mapped[str] = mapped_column(Text)
