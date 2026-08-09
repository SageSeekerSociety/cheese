"""Accept card PR fields (两阶段采纳 PR迭代式).

`AcceptStatus.pr_open` itself needs no migration (native_enum=False stores it
as a plain length-16 varchar). This migration only adds the columns that
track the GitHub PR / deploy-workflow state while a card sits in `pr_open`.

Revision ID: c7d8e9f0a1b2
Revises: 093133add3e1
Create Date: 2026-08-09 13:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "093133add3e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accept_cards", sa.Column("pr_number", sa.Integer(), nullable=True))
    op.add_column("accept_cards", sa.Column("pr_repo", sa.String(255), nullable=True))
    op.add_column("accept_cards", sa.Column("pr_url", sa.Text(), nullable=True))
    op.add_column(
        "accept_cards", sa.Column("pr_head_sha", sa.String(64), nullable=True)
    )
    # NULL while the PR's own CI is still pending; set once the PR itself has
    # merged and the card has moved on to waiting for the deploy workflow.
    op.add_column(
        "accept_cards",
        sa.Column("pr_merged_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accept_cards", "pr_merged_at")
    op.drop_column("accept_cards", "pr_head_sha")
    op.drop_column("accept_cards", "pr_url")
    op.drop_column("accept_cards", "pr_repo")
    op.drop_column("accept_cards", "pr_number")
