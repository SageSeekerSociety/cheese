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


class CustomRole(UuidPk, Timestamps, Base):
    """A custom expert role (spec §8.2 自定义角色).

    Claude Code agents-file semantics stored relationally: (name, title,
    description) mirror the frontmatter and ``body`` is the persona system
    prompt. NULL space_id = a personal role; set = owned by that institution.
    A custom role shadows a built-in library role with the same name (see
    app.domain.agent.roles.resolve_role_description).
    """

    __tablename__ = "custom_roles"

    space_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(128), default="")
