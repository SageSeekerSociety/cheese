"""merge native-input chain + agent-turn ledger heads

Revision ID: 833ab6058222
Revises: d6a32e4b9081, c9f4a2e7d153
Create Date: 2026-10-02 02:00:00.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "833ab6058222"
down_revision: str | Sequence[str] | None = ("d6a32e4b9081", "c9f4a2e7d153")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
