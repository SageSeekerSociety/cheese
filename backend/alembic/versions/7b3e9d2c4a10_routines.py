"""routines / routine_runs —— standing work on a clock or a project event

A routine is the rule a person confirmed; a run is one time it fired, unique per
(routine, occurrence) so a planned moment or an event is dispatched once.

Revision ID: 7b3e9d2c4a10
Revises: 52b13868b0e8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7b3e9d2c4a10"
down_revision: str | Sequence[str] | None = "52b13868b0e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "routines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "topic_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("context_scope", sa.Text(), nullable=False, server_default=""),
        sa.Column("output_dir", sa.String(300), nullable=False, server_default=""),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("agent_handle", sa.String(64), nullable=False),
        sa.Column("owner_handle", sa.String(64), nullable=False),
        sa.Column("proposed_by", sa.String(64), nullable=False),
        sa.Column("confirmed_by", sa.String(64), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_cursor", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_routines_project_id", "routines", ["project_id"])
    op.create_index("ix_routines_topic_id", "routines", ["topic_id"])
    op.create_index(
        "ix_routines_due",
        "routines",
        ["next_run_at"],
        postgresql_where=sa.text("state = 'active' AND next_run_at IS NOT NULL"),
    )
    op.create_table(
        "routine_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "routine_id",
            sa.Uuid(),
            sa.ForeignKey("routines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("occurrence_key", sa.String(300), nullable=False),
        sa.Column("trigger_detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("routine_revision", sa.BigInteger(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("delivery_event_id", sa.Uuid(), nullable=True),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("outputs", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint(
            "routine_id", "occurrence_key", name="uq_routine_occurrence"
        ),
    )
    op.create_index("ix_routine_runs_routine_id", "routine_runs", ["routine_id"])
    op.create_index(
        "ix_routine_runs_open",
        "routine_runs",
        ["created_at"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_table("routine_runs")
    op.drop_table("routines")
