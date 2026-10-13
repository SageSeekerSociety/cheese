"""Drop the accept-card note code for a closed, unmerged PR

A pending card whose PR was closed without merging used to stay pending with
this code, waiting for someone to reopen the PR. The poller now voids such a
card, so the code no longer exists. Rows still carrying it are cleared: the
next poll finds the PR closed and voids the card.

Revision ID: 7b2d4e9c1a60
Revises: 3e8b1c7d9a52
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7b2d4e9c1a60"
down_revision: str | Sequence[str] | None = "3e8b1c7d9a52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE accept_cards SET note_code = NULL "
        "WHERE note_code = 'pr_closed_unmerged'"
    )


def downgrade() -> None:
    """The cleared rows are not told apart from other NULL codes; nothing to restore."""
    pass
