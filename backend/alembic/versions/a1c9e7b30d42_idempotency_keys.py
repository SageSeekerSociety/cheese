"""idempotency_keys — one row per agent side effect that already happened

Revision ID: a1c9e7b30d42
Revises: c8b1f4a70d29
Create Date: 2026-08-11 16:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c9e7b30d42"
# Re-chained twice while this branch was open — b8e1d4c70a92 → c1d7e0a4b839 (#286)
# → c8b1f4a70d29 (#298). Each time, main landed a migration on the same parent
# this one had, and two children of one revision are two heads, at which point
# `upgrade head` refuses to run at all (single-head discipline,
# .claude/rules/migrations.md). The lesson is about TIMING, not about picking the
# right parent once: the fork appears between filing and merging, so the chain
# has to be re-pointed at whatever head main actually has at merge time.
down_revision: str | Sequence[str] | None = "b7e3c19d4f80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 重发 (④) re-runs a turn whose side effects may already have landed.
    # This table is the durable "already done" marker: the guard row and the
    # effect commit in ONE transaction, so a process death can never leave the
    # effect done and the marker missing.
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        # The first execution's output, replayed to a caller whose claim loses
        # so a retry gets the same answer instead of an error.
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # UNIQUE is the mechanism, not an optimisation: `ON CONFLICT DO NOTHING`
    # needs this index to be the arbiter that makes claim() atomic.
    op.create_index(
        op.f("ix_idempotency_keys_key"), "idempotency_keys", ["key"], unique=True
    )
    op.create_index(
        op.f("ix_idempotency_keys_scope_id"),
        "idempotency_keys",
        ["scope_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_idempotency_keys_created_at"),
        "idempotency_keys",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_idempotency_keys_created_at"), table_name="idempotency_keys")
    op.drop_index(op.f("ix_idempotency_keys_scope_id"), table_name="idempotency_keys")
    op.drop_index(op.f("ix_idempotency_keys_key"), table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
