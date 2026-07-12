"""agent_bindings + seed 芝士 as a real agent-user (agent-as-user, fusion-design §2)

Revision ID: c5f1a9d24e07
Revises: a1c9f3e70b21
Create Date: 2026-07-09 00:00:00.000000

An agent is a first-class user whose agent-ness is DERIVED from an execution
binding, never a column. This creates the ``agent_bindings`` table and seeds
芝士 (handle ``cheese``) as a real ``users`` row with one ``platform`` binding —
idempotently, so re-running or a pre-existing cheese row never duplicates.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5f1a9d24e07"
down_revision: str | Sequence[str] | None = "a1c9f3e70b21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = (
    "a95752502bb0"  # fusion A2: needs main user table
)


def upgrade() -> None:
    # Fusion A2: agent_bindings.user_id references the merged canonical identity
    # (main's ``user`` table, int PK) — agents are real users there. 芝士 itself is
    # seeded at boot by IdentityService.ensure_agent_user (into ``user``), so this
    # migration only creates the binding table (no cross-table seed needed).
    op.create_table(
        "agent_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_agent_binding_user"),
    )
    op.create_index(
        op.f("ix_agent_bindings_user_id"), "agent_bindings", ["user_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_bindings_user_id"), table_name="agent_bindings")
    op.drop_table("agent_bindings")
