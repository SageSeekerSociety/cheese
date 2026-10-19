"""merge the routines work with main again (a78c91d9518c + 5d3e8a1f2b7c)

Main moved its head once more while this branch was open. One empty revision
joins the chains: neither side touches the other's tables, and a downgrade
past it goes to whichever parent the target names.

Revision ID: e2600cb40ed8
Revises: a78c91d9518c, 5d3e8a1f2b7c
Create Date: 2026-10-19
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "e2600cb40ed8"
down_revision: str | Sequence[str] | None = ("a78c91d9518c", "5d3e8a1f2b7c")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Nothing to undo: a merge revision joins two chains, it runs no DDL."""
    pass
