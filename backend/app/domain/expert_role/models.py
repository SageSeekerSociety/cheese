"""Expert role model — spec §8.2.

芝士 isn't one persona — different projects load different expert roles. A role =
a set of preset skills + a role description, mapping directly to a Claude Code
agent type. Platform ships presets (计算机/设计/学术研究/创业…); leads/teachers can
define custom ones. A Task Template can name a default role.
"""

import uuid

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class ExpertRole(UuidPk, Timestamps, Base):
    __tablename__ = "expert_roles"

    # NULL project_id = platform-provided preset, available to all projects.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(64), index=True)
    role_description: Mapped[str] = mapped_column(Text, default="")
    # Preset skill names this role loads at start (spec §8.2 preset 部分).
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_preset: Mapped[bool] = mapped_column(default=False)
