"""turn_resumes: the resumes the platform owes a seat whose turn failed

Revision ID: c245f89aa6d7
Revises: 1478f65e1004
Create Date: 2026-10-17

One row per resume the platform schedules after a turn ends on a transient
model-service error (`app.domain.agent.turn_resume`). A new table with no
foreign keys, so no lock on a live table and no backfill.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c245f89aa6d7"
down_revision: str | Sequence[str] | None = "1478f65e1004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWED = "handed_at IS NULL AND dropped_at IS NULL"


def upgrade() -> None:
    op.create_table(
        "turn_resumes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_handle", sa.String(length=64), nullable=False),
        sa.Column("agent_instance_id", sa.Uuid(), nullable=False),
        sa.Column("failed_turn_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("said", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("handed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dropped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dropped_reason", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_turn_resumes_owed",
        "turn_resumes",
        ["conversation_id", "recipient_handle"],
        unique=True,
        postgresql_where=sa.text(_OWED),
    )
    op.create_index(
        "ix_turn_resumes_due",
        "turn_resumes",
        ["due_at"],
        postgresql_where=sa.text(_OWED),
    )
    op.create_index(
        "ix_turn_resumes_seat",
        "turn_resumes",
        ["conversation_id", "recipient_handle"],
    )


def downgrade() -> None:
    op.drop_index("ix_turn_resumes_seat", table_name="turn_resumes")
    op.drop_index("ix_turn_resumes_due", table_name="turn_resumes")
    op.drop_index("uq_turn_resumes_owed", table_name="turn_resumes")
    op.drop_table("turn_resumes")
