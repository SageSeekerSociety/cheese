"""merge duplicate merge-migrations (504ece6e60ea + c7d8e9f0a1b2)

Revision ID: fbd4bf2a51b6
Revises: 504ece6e60ea, c7d8e9f0a1b2
Create Date: 2026-08-10 00:17:31.478258

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "fbd4bf2a51b6"
down_revision: str | Sequence[str] | None = ("504ece6e60ea", "c7d8e9f0a1b2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
