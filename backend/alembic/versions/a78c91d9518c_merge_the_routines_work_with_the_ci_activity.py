"""merge the routines work with the ci-activity head (4479d91584fd + 32b875c31a9a)

Main added the CI-activity tables while this branch was open, so the two chains
fork again. One empty revision joins them: neither side touches the other's
tables, and a downgrade past it goes to whichever parent the target names.

Revision ID: a78c91d9518c
Revises: 4479d91584fd, 32b875c31a9a
Create Date: 2026-10-18
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "a78c91d9518c"
down_revision: str | Sequence[str] | None = ("4479d91584fd", "32b875c31a9a")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Nothing to undo: a merge revision joins two chains, it runs no DDL."""
    pass
