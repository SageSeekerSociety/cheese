"""Who may call a member's own agent

Revision ID: e81c4b7a2f05
Revises: 44de818574e0
Create Date: 2026-10-10

``own_agents.callers``: whoever its owner lets call it — ``owner`` (the owner
alone, what every existing one does today) or ``project`` (any person in the
project).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e81c4b7a2f05"
down_revision: str | Sequence[str] | None = "44de818574e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "own_agents",
        sa.Column("callers", sa.String(16), nullable=False, server_default="owner"),
    )
    op.create_check_constraint(
        "ck_own_agents_callers",
        "own_agents",
        "callers IN ('owner', 'project')",
        postgresql_not_valid=True,
    )
    with op.get_context().autocommit_block():
        op.execute("ALTER TABLE own_agents VALIDATE CONSTRAINT ck_own_agents_callers")


def downgrade() -> None:
    op.drop_constraint("ck_own_agents_callers", "own_agents", type_="check")
    op.drop_column("own_agents", "callers")
