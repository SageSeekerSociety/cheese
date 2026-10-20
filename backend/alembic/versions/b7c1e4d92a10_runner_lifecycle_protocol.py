"""the runner reports its session's lifecycle: session states, applied offsets,
and an input's failure after it was read

Revision ID: b7c1e4d92a10
Revises: b6e1d47c2a95
Create Date: 2026-10-19

``session_states`` holds each seat's session as its runner declares it, under
the worker epoch the platform issued it; ``session_offsets`` is how far the
platform applied what each registration reported, committed with the effects.
``native_inputs`` gains ``failed_at`` / ``failure``: an input read into work
that then failed. Its ``completed_at`` is no longer written or read (a read
input is delivered, nothing completes it); the column is dropped by a later
migration, once no release maps it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b7c1e4d92a10"
down_revision: str | Sequence[str] | None = "b6e1d47c2a95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("native_inputs")
    op.create_table(
        "session_states",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_handle", sa.String(length=64), nullable=False),
        sa.Column("harness", sa.String(length=32), nullable=False),
        sa.Column("worker_epoch", sa.BigInteger(), nullable=False),
        sa.Column("worker_status", sa.String(length=16), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exited_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "worker_status IN ('idle', 'running')",
            name="ck_session_states_worker_status",
        ),
        sa.PrimaryKeyConstraint("conversation_id", "agent_handle", "harness"),
    )
    op.create_index(
        "ix_session_states_running",
        "session_states",
        ["conversation_id"],
        postgresql_where=sa.text("worker_status = 'running'"),
    )
    op.create_table(
        "session_offsets",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_handle", sa.String(length=64), nullable=False),
        sa.Column("harness", sa.String(length=32), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("applied_seq", sa.BigInteger(), nullable=False),
        sa.Column("projected_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("conversation_id", "agent_handle", "harness", "epoch"),
    )
    op.add_column(
        "native_inputs",
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "native_inputs",
        sa.Column("failure", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    with_lock_retries("native_inputs")
    op.drop_column("native_inputs", "failure")
    op.drop_column("native_inputs", "failed_at")
    op.drop_table("session_offsets")
    op.drop_index("ix_session_states_running", table_name="session_states")
    op.drop_table("session_states")
