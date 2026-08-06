"""Memory model.

Per spec §8.4, the block tree (DB) is the source of truth; memory is a
projection for fast AI recall, rebuildable from blocks. Phase 0 stores memory
as plain entries scoped to a project or a user. The MemoryStore abstraction
(store.py) lets us swap in OpenViking later without touching callers.
"""

import enum

from sqlalchemy import Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class MemoryScope(enum.StrEnum):
    project = "project"  # 项目记忆: 章程/决策/进展 (spec §8.4)
    user = "user"  # 个人记忆: 跨项目, 跟着人走
    skill = "skill"  # 技能记忆: 领域知识/文档模板/场景包配置


class MemoryEntry(UuidPk, Timestamps, Base):
    __tablename__ = "memory_entries"

    scope: Mapped[MemoryScope] = mapped_column(
        Enum(MemoryScope, native_enum=False, length=16), index=True
    )
    # Project id or user handle, depending on scope.
    scope_id: Mapped[str] = mapped_column(String(128), index=True)
    content: Mapped[str] = mapped_column(Text)
