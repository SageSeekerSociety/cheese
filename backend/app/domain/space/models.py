from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    Sequence,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

space_seq = Sequence("space_seq")
space_categories_seq = Sequence("space_categories_seq")
space_user_rank_seq = Sequence("space_user_rank_seq")
space_admin_relation_seq = Sequence("space_admin_relation_seq")
space_domain_group_seq = Sequence("space_domain_group_seq")
space_domain_group_domain_seq = Sequence("space_domain_group_domain_seq")


class Space(Base):
    __tablename__ = "space"

    id: Mapped[int] = mapped_column(BigInteger, space_seq, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    intro: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enable_rank: Mapped[bool] = mapped_column(
        "enable_rank", Boolean, nullable=False, default=False
    )
    visible_task_limit: Mapped[int | None] = mapped_column(
        "visible_task_limit", Integer, nullable=True
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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SpaceCategory(Base):
    __tablename__ = "space_categories"

    id: Mapped[int] = mapped_column(BigInteger, space_categories_seq, primary_key=True)
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(
        "display_order", Integer, nullable=False, default=0
    )
    # Archived flag (SpaceCategory.isArchived in Kotlin)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 机构协议 (spec §4.2, #370): a 项目集 is where an institution states what it
    # provides and what it asks in return — 创研课 2026 秋 has twenty 赛题 and one
    # set of terms, so this is configured once here rather than twenty times
    # below. A single 赛题 may override it (`task.protocol_override`).
    # Resolved by app.domain.task.protocol.resolve; never read directly.
    resource_pack: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    conditions: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    default_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 壳: which of the platform's 壳 the projects under this 项目集 run. A NAME
    # into `app.domain.shell.catalog`, never a declaration — a 项目集 picks a 壳,
    # it does not ship one. Same placement and same whole-key override as the
    # three protocol fields above (a 创研课 has twenty 赛题 and one 壳). NULL =
    # nobody said, so `default` is in force.
    shell: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SpaceUserRank(Base):
    __tablename__ = "space_user_rank"

    id: Mapped[int] = mapped_column(BigInteger, space_user_rank_seq, primary_key=True)
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SpaceAdminRole(int, Enum):
    OWNER = 0
    ADMIN = 1


class SpaceAdminRelation(Base):
    __tablename__ = "space_admin_relation"

    id: Mapped[int] = mapped_column(
        BigInteger, space_admin_relation_seq, primary_key=True
    )
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SpaceDomainGroup(Base):
    __tablename__ = "space_domain_group"

    id: Mapped[int] = mapped_column(
        BigInteger, space_domain_group_seq, primary_key=True
    )
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SpaceDomainGroupDomain(Base):
    __tablename__ = "space_domain_group_domain"

    id: Mapped[int] = mapped_column(
        BigInteger, space_domain_group_domain_seq, primary_key=True
    )
    group_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SpaceClassificationTagRelation(Base):
    """Mirrors NT's `space_classification_topics_relation`. Many-to-many between
    Space and Topic, used by the frontend's `Space.classificationTopics` field.
    """

    __tablename__ = "space_classification_tag_relation"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tag_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
