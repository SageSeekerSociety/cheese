"""merge main again into the routines work (9f2c7b1e4a63 + 569e06806d03)

Main dropped the knowledge tables while this branch was open; the branch's
own head was already a merge revision. One more empty revision joins the two
chains again — neither side touches the other's tables, so nothing is
restored by a downgrade.

Revision ID: 4479d91584fd
Revises: 9f2c7b1e4a63, 569e06806d03
Create Date: 2026-10-18
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "4479d91584fd"
down_revision: str | Sequence[str] | None = ("9f2c7b1e4a63", "569e06806d03")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Nothing to undo: a merge revision joins two chains, it runs no DDL."""
    pass
