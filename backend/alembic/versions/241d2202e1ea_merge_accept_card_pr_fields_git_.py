"""merge accept-card-pr-fields + git-installations heads

Revision ID: 241d2202e1ea
Revises: b3d5f7a9c102, e3a94c6f5b18
Create Date: 2026-08-09 20:31:14.479867

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "241d2202e1ea"
down_revision: str | Sequence[str] | None = ("b3d5f7a9c102", "e3a94c6f5b18")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
