"""Resource usage — LLM token/cost accounting (spec §9.1 三级可见性, §10.2).

One row per agent turn. Aggregated at topic / project / Space levels so usage is
visible at each level (话题→项目→机构).
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class GrantSource(StrEnum):
    """Where a credit pack came from; it decides when the pack lapses and where
    it falls in the order a charge drains packs in (``usage.ledger``)."""

    # The team's plan, one per period; lapses when the period ends.
    PLAN_PERIOD = "plan_period"
    # A 赛题's 资源包 for one project; spendable only there.
    TASK_EARMARK = "task_earmark"
    PURCHASE = "purchase"
    ADMIN_GRANT = "admin_grant"


class Plan(Timestamps, Base):
    """A credit plan, a record an administrator edits rather than code (#2397).

    Every team is on one (``team.plan_key``). A plan issues ``credits_per_period``
    credits to each of its teams every ``period``, may cap spending inside time
    windows, and says which model tiers its teams may use. ``unlimited`` plans
    issue nothing and refuse nothing.
    """

    __tablename__ = "plans"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    # Who may be put on it: "personal" teams, shared "team"s, or "both".
    audience: Mapped[str] = mapped_column(String(16), default="both")
    # The plan's pack per period; NULL issues none.
    credits_per_period: Mapped[float | None] = mapped_column(Float, nullable=True)
    period: Mapped[str] = mapped_column(String(16), default="month")
    # ``[{"hours": 5, "credits": 100}, ...]``: at most that many credits within
    # any window of that many hours.
    windows: Mapped[list] = mapped_column(JSON, default=list)
    # The model tiers its teams may use, the subscription (Claude) models
    # included; NULL is every tier.
    model_tiers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    unlimited: Mapped[bool] = mapped_column(Boolean, default=False)
    # Only an administrator can put a team on it, and only the console lists it.
    admin_only: Mapped[bool] = mapped_column(Boolean, default=False)


class CreditAdminAudit(UuidPk, Timestamps, Base):
    """Every change an administrator makes to plans and credit packs: who, to
    what, and what it was before and after."""

    __tablename__ = "credit_admin_audit"
    __table_args__ = (
        Index("ix_credit_admin_audit_created_at", text("created_at DESC")),
    )

    actor_handle: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(128))
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ComputeGrant(UuidPk, Timestamps, Base):
    """A credit pack: a balance that spending draws down.

    Every pack belongs to a team, never to a person inside one; a person's own
    credits are packs on their personal team. ``project_id`` narrows a pack to
    one project of the team (a task's earmark). ``expires_at`` is when it
    lapses; NULL never does.
    """

    __tablename__ = "compute_grants"
    __table_args__ = (
        # One plan pack per team per period, so issuing it is an insert that a
        # concurrent first request can lose without writing a second one.
        Index(
            "uq_compute_grants_plan_period",
            "team_id",
            "period_start",
            unique=True,
            postgresql_where=text("source = 'plan_period'"),
        ),
        CheckConstraint(
            "source IN ('plan_period', 'task_earmark', 'purchase', 'admin_grant')",
            name="ck_compute_grants_source",
        ),
    )

    team_id: Mapped[int] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(32))
    # The 赛题 whose 项目集 funded this grant (#370). An int, and deliberately
    # NOT a foreign key: the credits were granted, so the audit trail has to
    # survive the 赛题 being deleted.
    source_task_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # The first day of the period a plan pack is for, in the platform's timezone.
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Why an administrator issued it (a contract, a refund); shown only to
    # administrators.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    credits_total: Mapped[float] = mapped_column(Float)
    credits_used: Mapped[float] = mapped_column(Float, default=0.0)


class ResourceUsage(UuidPk, Timestamps, Base):
    __tablename__ = "resource_usage"
    #: 平台看板按天聚合这张表：`created_at` 的范围扫。等值的四条索引
    #: （`project_id` / `topic_id` / `task_id` / `turn_id`）一条都服务不了它——
    #: 这是全仓增长最快的一张表，没有它就是每次看板全表顺序扫。迁移见
    #: `a9c4e7f12b60`。
    __table_args__ = (
        Index("ix_resource_usage_created_at", "created_at"),
        # A person's spend in a month: the personal usage page reads it.
        Index("ix_resource_usage_user_created_at", "user_id", "created_at"),
        # A team's spend inside a plan's time window, read without the rows.
        Index(
            "ix_resource_usage_team_created_at",
            "team_id",
            "created_at",
            postgresql_include=["credits"],
        ),
    )

    # NULL when the spend happened outside any project (see ``user_id``).
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Who asked, for spend outside a project; their personal team paid. NULL
    # inside a project, whose spend is not split by person (#394).
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=True
    )
    # The room the spend happened in; NULL when it cannot be attributed at all.
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Which piece of work inside it. NULL is the room's own main line — the
    # distinction matters here because "what did this task cost" is a question
    # people ask, and a room-level total cannot answer it.
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # The human message or platform work id this spend belongs to. One attributed
    # unit can write more than one row because the metering proxy logs every
    # /v1/messages call and the gateway can land a deferred backfill. The column
    # name stays for storage and protocol compatibility. NULL means the supply
    # cannot be attributed; each such row remains one unit in aggregate reports.
    turn_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(64), default="")
    # Every prompt token, cached ones included; the two cache columns are the
    # shares of it read from and written to the provider's cache, 0 where the
    # supply reports only the total (the gateway's daily drain).
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    cache_write_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    kind: Mapped[str] = mapped_column(String(24), default="chat")
    # The supply the traffic actually took (issue #218): "gateway" (LiteLLM),
    # "subscription" (metering proxy), "native" (profile-pinned credentials).
    # "" on rows that predate the column.
    route: Mapped[str] = mapped_column(String(16), default="")
    # The team that paid, and the credits charged for this row (#2397). NULL
    # and 0 on rows written before they were recorded.
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), nullable=True
    )
    credits: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")


class IngestCheckpoint(Base):
    """Exactly-once progress marker for an append-only usage log (issue #218).

    One row per source file. ``byte_offset`` is how far ingestion has consumed;
    ``fingerprint`` hashes the file's first bytes so a rotated/replaced file
    reads as a new generation and restarts from zero — the alternative is
    silently skipping (offset past a shorter file) or double-billing (offset
    reset against the same file). Committed in the SAME transaction as the rows
    it covers, which is the whole exactly-once argument.
    """

    __tablename__ = "ingest_checkpoints"

    source: Mapped[str] = mapped_column(String(128), primary_key=True)
    byte_offset: Mapped[int] = mapped_column(BigInteger, default=0)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
