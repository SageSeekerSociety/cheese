"""Agent type — 出厂设置, the half of an agent that is not this project's.

A type says who an agent is (``body``, its system prompt), what it can reach
(``skills`` / ``mcp_servers``) and how it runs (``model`` / ``effort`` /
``harness``). It holds no memory and names no project, which is what lets one
type back an agent in every project at once — the memory it accumulates lives
on the :class:`~app.domain.agent_instance.models.AgentInstance` instead.

Presets ship as files (``library.py``); this table is where a person's own type
lives. A row shadows a preset of the same name, so overriding a preset never
means forking the file.
"""

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class AgentType(UuidPk, Timestamps, Base):
    __tablename__ = "agent_types"

    # NULL space_id = a personal type; set = owned by that institution.
    space_id: Mapped[int | None] = mapped_column(
        ForeignKey("space.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The slug an instance names its type by, so it is unique platform-wide.
    name: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # The system prompt this agent runs under.
    body: Mapped[str] = mapped_column(Text, default="")
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    mcp_servers: Mapped[list[str]] = mapped_column(JSON, default=list)
    # NULL = "this type does not care" — the deployment's own choice stands.
    # Pinning a default here would quietly override every deployment that ships
    # a different model, which is the opposite of what a type is for.
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    harness: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), default="")
