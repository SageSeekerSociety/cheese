"""fusion: unify product + cheesex migration chains

Revision ID: 4006c4e8b583
Revises: d3b8f1a20c11, f6b7c8d9e0a1
Create Date: 2026-07-11 01:07:01.094119

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "4006c4e8b583"
down_revision: str | Sequence[str] | None = ("d3b8f1a20c11", "f6b7c8d9e0a1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
