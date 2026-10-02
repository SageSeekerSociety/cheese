"""Plans bill one way: a monthly pack, or time windows; plans get a rank

Revision ID: b0caecc81d00
Revises: 650de6af85af
Create Date: 2026-10-03

A plan either issues a monthly pack or lets its teams spend up to a cap inside
time windows; it no longer does both. A plan an administrator gave both keeps
its pack and loses its windows, which is how it billed: the windows only
narrowed what the pack allowed.

plan_window_use keeps each team's spend in each window this round, so a
window can start at a team's first call and count only what the plan covers.
rank orders plans for the console and for naming the plan a model needs;
Reserve, for the platform's own team, goes last.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b0caecc81d00"
down_revision: str | Sequence[str] | None = "650de6af85af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE plans SET rank = 100 WHERE key = 'reserve'")
    op.execute(
        "UPDATE plans SET windows = '[]'::json "
        "WHERE credits_per_period IS NOT NULL AND windows::text <> '[]'"
    )
    op.create_table(
        "plan_window_use",
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("team.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("window", sa.String(16), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("credits_used", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("plan_window_use")
    op.drop_column("plans", "rank")
