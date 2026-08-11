"""merge webhook-tokens + git-installations heads (main deploy fix)

Revision ID: b2958d64e679
Revises: e1f2a3b4c5d6, e3a94c6f5b18
Create Date: 2026-08-09 20:50:42.779965

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "b2958d64e679"
down_revision: str | Sequence[str] | None = ("e1f2a3b4c5d6", "e3a94c6f5b18")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
