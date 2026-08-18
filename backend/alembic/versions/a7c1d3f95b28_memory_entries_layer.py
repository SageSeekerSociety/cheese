"""memory_entries.layer — split the always-injected core from the rest

Revision ID: a7c1d3f95b28
Revises: c3e8b2d94f61
Create Date: 2026-08-18 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c1d3f95b28"
down_revision: str | Sequence[str] | None = "c3e8b2d94f61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Everything written so far was written as "a thing I learned" — nobody has
    # yet had the chance to say a fact is standing policy — so every existing
    # row backfills to `fact` and the core layer starts empty. That is the safe
    # direction: a fact wrongly marked core would be paid for on every turn
    # forever, while a core fact left as a fact is still retrievable.
    op.add_column(
        "memory_entries",
        sa.Column(
            "layer",
            sa.Enum("core", "fact", native_enum=False, length=8),
            nullable=False,
            server_default="fact",
        ),
    )
    # Injection reads one layer of one pool at a time, so the layer belongs in
    # the same index as the pool key; the standalone scope/scope_id indexes the
    # table already carries cannot answer that without a heap scan.
    op.create_index(
        "ix_memory_entries_pool_layer",
        "memory_entries",
        ["scope", "scope_id", "layer"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_memory_entries_pool_layer", table_name="memory_entries")
    op.drop_column("memory_entries", "layer")
