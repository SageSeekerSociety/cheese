"""Cloud compute is metered in runs and charged in credits (#2320 计费).

``compute_runs`` holds one row per stretch a cloud sandbox or a whole cloud VM
ran for a project, and how far it has been charged.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e7c41a9b2d58"
down_revision = "7d3a9c61e2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "compute_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("spec", sa.String(64), nullable=False),
        sa.Column("subject", sa.String(64), nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "topic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("topics.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("billed_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("credits", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('sandbox', 'vm')", name="ck_compute_runs_kind"),
    )
    op.create_index("ix_compute_runs_project_id", "compute_runs", ["project_id"])
    op.create_index(
        "uq_compute_runs_open_subject",
        "compute_runs",
        ["subject"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ix_compute_runs_unsettled",
        "compute_runs",
        ["billed_until"],
        postgresql_where=sa.text("ended_at IS NULL OR billed_until < ended_at"),
    )


def downgrade() -> None:
    op.drop_table("compute_runs")
