"""merge FB-56 provenance chain + upstream heads

Revision ID: c9f4a2e7d153
Revises: c5835976a247, f7a1c3e9d802
Create Date: 2026-10-01 15:20:00.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "c9f4a2e7d153"
down_revision: str | Sequence[str] | None = ("c5835976a247", "f7a1c3e9d802")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
