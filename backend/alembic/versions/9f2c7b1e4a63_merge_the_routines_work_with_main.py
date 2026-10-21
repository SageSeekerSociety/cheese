"""merge the routines work with main (7c2f4a8e1b93 + 7c3e5a1f9b24)

The routine-templates chain and main's own head, joined by one empty revision.
Neither side's tables come near the other's — this branch adds
``routine_templates``, main's head adds the project-situation tables — so
nothing is restored by a downgrade: `alembic downgrade` to either parent picks
whichever of the two chains it names and leaves the other's tables standing,
which is what the parents already did on their own.

Revision ID: 9f2c7b1e4a63
Revises: 7c2f4a8e1b93, 7c3e5a1f9b24
Create Date: 2026-10-16 17:22:41.000000
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "9f2c7b1e4a63"
down_revision: str | Sequence[str] | None = ("7c2f4a8e1b93", "7c3e5a1f9b24")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Nothing to undo: a merge revision joins two chains, it runs no DDL.

    A downgrade past it goes to whichever parent the target names — the two
    chains stay independent, exactly as they were before the merge.
    """
    pass
