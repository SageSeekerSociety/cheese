"""Resource usage — LLM token/cost accounting (spec §9.1 三级可见性, §10.2).

One row per agent turn. Aggregated at topic / project / Space levels so usage is
visible at each level (话题→项目→机构).
"""

import uuid

from sqlalchemy import BigInteger, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class ComputeGrant(UuidPk, Timestamps, Base):
    """Team credits, optionally restricted to a funded project.

    A task's resource pack keeps its project restriction. General grants have
    no project_id and can be consumed by every project in the owning team.
    """

    __tablename__ = "compute_grants"

    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # NULL is team-wide. Old owner-less projects can retain restricted grants.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # The 赛题 whose 项目集 funded this grant (#370). An int, and deliberately
    # NOT a foreign key: the credits were granted, so the audit trail has to
    # survive the 赛题 being deleted. It pointed at cheesex `tasks.id` (uuid)
    # until that hierarchy was retired.
    source_task_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    credits_total: Mapped[float] = mapped_column(Float)
    credits_used: Mapped[float] = mapped_column(Float, default=0.0)


class ResourceUsage(UuidPk, Timestamps, Base):
    __tablename__ = "resource_usage"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
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
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    kind: Mapped[str] = mapped_column(String(24), default="chat")
    # The supply the traffic actually took (issue #218): "gateway" (LiteLLM),
    # "subscription" (metering proxy), "native" (profile-pinned credentials).
    # "" on rows that predate the column.
    route: Mapped[str] = mapped_column(String(16), default="")


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
