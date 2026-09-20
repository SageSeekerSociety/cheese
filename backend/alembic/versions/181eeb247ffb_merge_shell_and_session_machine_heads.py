"""merge shell-on-space-categories + session-records-its-machine heads

Revision ID: 181eeb247ffb
Revises: b7c4e19f2a83, c8a1d5e73f20
Create Date: 2026-09-20

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "181eeb247ffb"
down_revision: str | Sequence[str] | None = ("b7c4e19f2a83", "c8a1d5e73f20")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
