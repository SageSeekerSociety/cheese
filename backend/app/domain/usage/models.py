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
    """A compute-credit grant issued to a project (spec §9.1 机构提供算力).

    Issued when a project links a Task whose Template's resource_pack carries
    {"compute_credits": N} — the protocol's 资源包 made real. A project with NO
    grants is unlimited (spec §4 项目自治: an unlinked personal project is never
    metered). Turn token usage is folded into credits and deducted oldest grant
    first; the newest grant may over-run its total so consumption stays truthful.
    """

    __tablename__ = "compute_grants"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The task whose template funded this grant. Kept on task deletion (the
    # credits were granted; the audit trail should survive the source).
    source_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    credits_total: Mapped[float] = mapped_column(Float)
    credits_used: Mapped[float] = mapped_column(Float, default=0.0)


class ResourceUsage(UuidPk, Timestamps, Base):
    __tablename__ = "resource_usage"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # The agent turn this spend belongs to. A turn writes MORE than one row —
    # the metering proxy logs every /v1/messages call, the gateway lands a
    # deferred backfill row later — so counting rows counted 43 "turns" for a
    # 3-turn topic. NULL where the supply cannot be attributed (rows predating
    # this column, or proxy lines outside any known turn); those still count as
    # one turn each, which is the old behaviour and the honest floor.
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
