"""A way of working a project saved to reuse: a native skill its sessions load.

The row is the editable source; every confirmed version is kept as a revision
so an earlier one can be read or restored. Only a confirmed skill is shipped.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class ProjectSkill(UuidPk, Timestamps, Base):
    __tablename__ = "project_skills"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_project_skill_name"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    #: The skill's folder name and the name a session invokes it by.
    name: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    #: 用途 — also the native skill's description, which decides when it is used.
    description: Mapped[str] = mapped_column(Text)
    inputs: Mapped[str] = mapped_column(Text, default="", server_default="")
    steps: Mapped[str] = mapped_column(Text)
    outputs: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: {relative path: text} shipped beside SKILL.md (scripts, templates, notes).
    files: Mapped[dict] = mapped_column(JSONB, default=dict)
    #: draft (proposed or edited by an AI teammate, not shipped) | active.
    state: Mapped[str] = mapped_column(String(16), default="draft")
    source_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )
    proposed_by: Mapped[str] = mapped_column(String(64))
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The confirmed revision sessions currently get; 0 before the first.
    shipped_revision: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )


class ProjectSkillRevision(UuidPk, Base):
    __tablename__ = "project_skill_revisions"
    __table_args__ = (
        UniqueConstraint("skill_id", "revision", name="uq_project_skill_revision"),
    )

    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("project_skills.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(BigInteger)
    #: title, description, inputs, steps, outputs, files as confirmed.
    content: Mapped[dict] = mapped_column(JSONB)
    confirmed_by: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
