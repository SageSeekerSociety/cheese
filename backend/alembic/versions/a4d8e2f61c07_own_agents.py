"""A project's agent that is one member's own coding agent

Revision ID: a4d8e2f61c07
Revises: c3f1a7d2e9b4
Create Date: 2026-10-07

``own_agents``: which of a project's agents is a member's own Claude Code, run
on their machine with their login and called by them alone (#2991).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4d8e2f61c07"
down_revision: str | Sequence[str] | None = "c3f1a7d2e9b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "own_agents",
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("harness", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["agent_instances.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("instance_id"),
    )
    op.create_index("ix_own_agents_owner_user_id", "own_agents", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_own_agents_owner_user_id", table_name="own_agents")
    op.drop_table("own_agents")
