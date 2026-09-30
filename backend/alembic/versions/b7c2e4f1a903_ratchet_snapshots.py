"""ratchet snapshots

Revision ID: b7c2e4f1a903
Revises: 4c9f3a81b42d

The architecture ratchet's collected snapshots, one row per CI run of
`arch-metrics.yml` (`app.domain.ratchet`). Kept here rather than read live from
GitHub because an Actions artifact expires after 90 days, and a series whose
oldest points vanish on their own reads as a project that started measuring when
the last cleanup ran.

`uq_ratchet_snapshots_run` is the whole idempotency story: a pull and the page's
refresh button can overlap, and the loser inserts nothing.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "b7c2e4f1a903"
down_revision: str | Sequence[str] | None = "4c9f3a81b42d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ratchet_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo", sa.String(200), nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=False),
        sa.Column("commit_sha", sa.String(64), nullable=False),
        sa.Column("commit_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot_version", sa.Integer(), nullable=True),
        sa.Column("collection", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("run_url", sa.String(500), nullable=True),
        sa.Column("artifact_id", sa.BigInteger(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repo", "workflow_run_id", name="uq_ratchet_snapshots_run"),
    )
    op.create_index(
        "ix_ratchet_snapshots_repo_collected",
        "ratchet_snapshots",
        ["repo", "collected_at"],
    )
    op.create_index(
        "ix_ratchet_snapshots_repo_commit",
        "ratchet_snapshots",
        ["repo", "commit_sha"],
    )


def downgrade() -> None:
    op.drop_index("ix_ratchet_snapshots_repo_commit", table_name="ratchet_snapshots")
    op.drop_index("ix_ratchet_snapshots_repo_collected", table_name="ratchet_snapshots")
    op.drop_table("ratchet_snapshots")
