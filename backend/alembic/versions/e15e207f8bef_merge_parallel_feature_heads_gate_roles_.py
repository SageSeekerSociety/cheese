"""merge parallel feature heads (gate, roles+grants, market)

Revision ID: e15e207f8bef
Revises: 24f052347875, a7c31f92e6d0, f2d7c410a9e3
Create Date: 2026-07-04 00:33:35.618622

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "e15e207f8bef"
down_revision: str | Sequence[str] | None = (
    "24f052347875",
    "a7c31f92e6d0",
    "f2d7c410a9e3",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
