"""merge accept-card-pr-fields + upstream-sync heads (main deploy fix 2)

Revision ID: 504ece6e60ea
Revises: b3d5f7a9c102, f9a1c7e3b502
Create Date: 2026-08-09 22:37:58.006846

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "504ece6e60ea"
down_revision: str | Sequence[str] | None = ("b3d5f7a9c102", "f9a1c7e3b502")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
