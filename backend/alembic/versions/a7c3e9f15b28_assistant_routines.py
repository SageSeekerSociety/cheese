"""A person's 芝士 on its own: routines and their runs

Revision ID: a7c3e9f15b28
Revises: b41d7a9c2e63
Create Date: 2026-10-22

``assistant_routines`` is what a person's 芝士 does on a clock or when
something happens to them; ``assistant_runs`` is each time it went to work on
its own. Two new, empty tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7c3e9f15b28"
down_revision: str | Sequence[str] | None = "b41d7a9c2e63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_routines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("words", sa.Text(), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "state", sa.String(length=16), server_default="active", nullable=False
        ),
        sa.Column("paused", sa.String(length=16), server_default="", nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_cursor", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            ondelete="CASCADE",
            name="fk_assistant_routines_user_id_user",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["assistant_conversations.id"],
            ondelete="SET NULL",
            name="fk_assistant_routines_conversation_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_routines_user_id", "assistant_routines", ["user_id"])
    op.create_index(
        "ix_assistant_routines_due",
        "assistant_routines",
        ["next_run_at"],
        postgresql_where=sa.text("state = 'active' AND next_run_at IS NOT NULL"),
    )
    op.create_index(
        "ix_assistant_routines_conversation_id",
        "assistant_routines",
        ["conversation_id"],
    )
    op.create_table(
        "assistant_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("routine_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("asked", sa.Integer(), nullable=False),
        sa.Column("told", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            ondelete="CASCADE",
            name="fk_assistant_runs_user_id_user",
        ),
        sa.ForeignKeyConstraint(
            ["routine_id"],
            ["assistant_routines.id"],
            ondelete="CASCADE",
            name="fk_assistant_runs_routine_id",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["assistant_conversations.id"],
            ondelete="SET NULL",
            name="fk_assistant_runs_conversation_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "occurrence", name="uq_assistant_runs_occurrence"
        ),
    )
    op.create_index(
        "ix_assistant_runs_routine_id", "assistant_runs", ["routine_id", "created_at"]
    )
    op.create_index(
        "ix_assistant_runs_open",
        "assistant_runs",
        ["user_id"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_assistant_runs_conversation_id", "assistant_runs", ["conversation_id"]
    )


def downgrade() -> None:
    op.drop_table("assistant_runs")
    op.drop_table("assistant_routines")
