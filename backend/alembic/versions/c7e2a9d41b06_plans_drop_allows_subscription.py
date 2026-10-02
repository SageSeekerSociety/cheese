"""Plans: drop allows_subscription; model tiers alone say what a plan may use

Revision ID: c7e2a9d41b06
Revises: b42af333ed1d
Create Date: 2026-10-02

A plan limits models by tier only. The Claude subscription models carry tiers
of their own (Sonnet and Opus premium, Fable frontier), so a separate switch for
subscription use said the same thing a second time.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7e2a9d41b06"
down_revision: str | Sequence[str] | None = "b42af333ed1d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("plans", "allows_subscription")


def downgrade() -> None:
    op.add_column(
        "plans",
        sa.Column(
            "allows_subscription",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute("UPDATE plans SET allows_subscription = unlimited")
    op.alter_column("plans", "allows_subscription", server_default=None)
