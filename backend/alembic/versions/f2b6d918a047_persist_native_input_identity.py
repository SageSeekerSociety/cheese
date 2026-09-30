"""Persist input identity independently of the backend process that sent it.

Revision ID: f2b6d918a047
Revises: e5a1c7d3b284
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f2b6d918a047"
down_revision = "e5a1c7d3b284"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "native_inputs",
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_handle", sa.String(64), nullable=False),
        sa.Column("harness", sa.String(32), nullable=False),
        sa.Column("native_session_id", sa.String(256), nullable=False),
        sa.Column("input_id", sa.Uuid(), nullable=False),
        sa.Column("work_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_id", sa.Uuid(), nullable=True),
        sa.Column("attempt_id", sa.Uuid(), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("block_ids", postgresql.JSONB(), nullable=False),
        sa.Column("seen_block_ids", postgresql.JSONB(), nullable=False),
        sa.Column("seen_by", sa.String(64), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("echoed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "harness", "native_session_id", "input_id", name="uq_native_input_identity"
        ),
    )
    op.create_index("ix_native_inputs_delivery", "native_inputs", ["delivery_id"])


def downgrade() -> None:
    op.drop_index("ix_native_inputs_delivery", table_name="native_inputs")
    op.drop_table("native_inputs")
