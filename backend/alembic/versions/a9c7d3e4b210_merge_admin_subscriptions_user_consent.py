"""Merge admin subscription and current main migration heads.

Revision ID: a9c7d3e4b210
Revises: eeebfb49d703, 9d6c2a47e0b1
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "a9c7d3e4b210"
down_revision: str | Sequence[str] | None = ("eeebfb49d703", "9d6c2a47e0b1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
