"""converge twin duplicate merges (fbd4bf2a51b6 + c2f677c441f0)

Revision ID: b5045bf862fe
Revises: c2f677c441f0, fbd4bf2a51b6
Create Date: 2026-08-10 02:10:50.981352

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "b5045bf862fe"
down_revision: str | Sequence[str] | None = ("c2f677c441f0", "fbd4bf2a51b6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
