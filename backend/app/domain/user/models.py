"""User model.

A platform member. Spec §7.2: 个人主页 = LinkedIn/GitHub profile; 入驻时填
兴趣/技能/方向, 之后芝士在协作中加深理解 (个人记忆, spec §8.4, stored separately
in memory_entries with scope=user). Phase 0 has no auth; `handle` is the stable
identity used as block author and memory scope_id.
"""

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class User(UuidPk, Timestamps, Base):
    __tablename__ = "users"

    # Stable login-free identity (e.g. "user-1"); also used as block author.
    handle: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bio: Mapped[str] = mapped_column(Text, default="")
    # Self-declared starting points (spec §8.4): interests/skills/directions.
    interests: Mapped[list[str]] = mapped_column(JSON, default=list)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
