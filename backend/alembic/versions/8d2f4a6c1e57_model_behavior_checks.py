"""model_behavior_checks: what an AI teammate was asked to do and whether it did

A new table, so no backfill: rows start with the first ask after deploy.

Revision ID: 8d2f4a6c1e57
Revises: 7c3e5a1f9b24
Create Date: 2026-10-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8d2f4a6c1e57"
down_revision: str | Sequence[str] | None = "7c3e5a1f9b24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_behavior_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("ask_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_handle", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "ask_id", name="uq_model_behavior_checks_kind_ask"),
    )
    op.create_index(
        "ix_model_behavior_checks_kind_asked_at",
        "model_behavior_checks",
        ["kind", "asked_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_behavior_checks_kind_asked_at", table_name="model_behavior_checks"
    )
    op.drop_table("model_behavior_checks")
