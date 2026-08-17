"""accept_cards.pr_authorized_sha — freeze the commit the human authorized

Revision ID: e7f3a90c15d2
Revises: d4a1b6f27c90
Create Date: 2026-08-10 15:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f3a90c15d2"
down_revision: str | Sequence[str] | None = "d4a1b6f27c90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 人类授权动作前移 (2026-08-10): the human's click now buys "open the PR and
    # let CI run", so the machine may merge later without asking again — but
    # only while the PR still contains what was authorized. `pr_head_sha` moves
    # with every fix pushed afterwards, so the authorized commit has to be kept
    # separately; see models.AcceptCard for why nothing else can stand in.
    # Nullable, no backfill: cards already in flight (`pr_open`) must not be
    # interrupted by this change — the poller adopts their current head as the
    # baseline on its next tick.
    op.add_column(
        "accept_cards",
        sa.Column("pr_authorized_sha", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("accept_cards", "pr_authorized_sha")
