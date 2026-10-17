"""overseas_model_approvals: the teams an administrator let use overseas models

Revision ID: 3f7a9c2e1d04
Revises: d4c8e1a7b3f2
Create Date: 2026-10-15

One row per approved team. No backfill: every team starts unapproved, and an
administrator approves the ones that should keep Claude from the console.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "3f7a9c2e1d04"
down_revision: str | Sequence[str] | None = "d4c8e1a7b3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("team")
    op.create_table(
        "overseas_model_approvals",
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "team.id",
                ondelete="CASCADE",
                name="fk_overseas_model_approvals_team_id_team",
            ),
            primary_key=True,
        ),
        sa.Column("approved_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("overseas_model_approvals")
