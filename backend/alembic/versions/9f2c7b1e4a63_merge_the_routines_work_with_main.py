"""merge the routines work with main (7c2f4a8e1b93 + b34ba429e24d)

Revision ID: 9f2c7b1e4a63
Revises: 7c2f4a8e1b93, b34ba429e24d
Create Date: 2026-10-16 17:22:41.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "9f2c7b1e4a63"
down_revision: str | Sequence[str] | None = ('7c2f4a8e1b93', '7c3e5a1f9b24')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
