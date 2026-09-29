"""join the task search BM25 index back onto the chain

#2061 added 19fe29bcb352 on 7b2d4e9c1a60. It was reverted (#2074) after dev
had already applied it, and 2e74669241f9 landed on 7b2d4e9c1a60 meanwhile.
Both are on main and 19fe29bcb352 is stamped on dev, so neither can be
rechained; this joins them. dev runs 2e74669241f9 then this; a fresh database
runs both branches.

Revision ID: 06b31e24626f
Revises: 2e74669241f9, 19fe29bcb352
Create Date: 2026-09-29 14:29:59.827105

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "06b31e24626f"
down_revision: str | Sequence[str] | None = ("2e74669241f9", "19fe29bcb352")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
