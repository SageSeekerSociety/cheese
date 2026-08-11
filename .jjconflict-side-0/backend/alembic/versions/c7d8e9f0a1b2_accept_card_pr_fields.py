"""Accept card PR fields (两阶段采纳 PR迭代式).

`AcceptStatus.pr_open` itself needs no migration (native_enum=False stores it
as a plain length-16 varchar). `pr_number`/`pr_url` already exist as of
b3d5f7a9c102 (#188 §5.1's own PR-based accept, landed independently on main
while this branch was in flight) — this migration only adds the NEW columns
the two-phase (PR迭代式) flow needs on top: which repo, the head commit being
polled, and when the PR itself merged (vs. the deploy workflow it triggers).
Also merges in f9a1c7e3b502 (oauth access_token column) — both that migration
and b3d5f7a9c102 independently re-merged the same e1f2a3b4c5d6/e3a94c6f5b18
fork point this branch's own now-deleted 093133add3e1 did, so this is the
single point where all three lines finally join back into one.

Revision ID: c7d8e9f0a1b2
Revises: f9a1c7e3b502, b3d5f7a9c102
Create Date: 2026-08-09 13:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = ("f9a1c7e3b502", "b3d5f7a9c102")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accept_cards", sa.Column("pr_repo", sa.String(255), nullable=True))
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
    op.drop_column("accept_cards", "pr_repo")
