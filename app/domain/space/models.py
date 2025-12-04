from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from enum import Enum


class Base(DeclarativeBase):
    """Base declarative for space domain."""


class Space(Base):
    __tablename__ = "space"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    intro: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enable_rank: Mapped[bool] = mapped_column(
        "enable_rank", Boolean, nullable=False, default=False
    )
    # Kotlin Space.defaultCategory -> default_category_id column
    default_category_id: Mapped[int | None] = mapped_column(
        "default_category_id", Integer, nullable=True
    )
    announcements: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    task_templates: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class SpaceCategory(Base):
    __tablename__ = "space_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(
        "display_order", Integer, nullable=False, default=0
    )
    # Archived flag (SpaceCategory.isArchived in Kotlin)
    archived_at: Mapped[datetime | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class SpaceUserRank(Base):
    __tablename__ = "space_user_rank"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class SpaceAdminRole(str, Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"


class SpaceAdminRelation(Base):
    __tablename__ = "space_admin_relation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=SpaceAdminRole.ADMIN.value)

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
