"""Projects keep only a default work computer, no saved favorites

Revision ID: 0f8a616e288e
Revises: e7c2a9d41f58
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0f8a616e288e"
down_revision: str | Sequence[str] | None = "e7c2a9d41f58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the saved favorites from every project's compute settings.

    A project now keeps only the default its new agents start on; rooms pick
    any configuration directly. The settings model no longer accepts
    `favorites`, so a row still holding one would fail to load. The previous
    backend image treats a missing list as empty, so it keeps working while
    this runs.
    """
    # `settings` is json, not jsonb, and the key-delete operator is jsonb-only.
    op.execute(
        """
        UPDATE projects
        SET settings = jsonb_set(
            settings::jsonb,
            '{compute_configs}',
            (settings::jsonb -> 'compute_configs') - 'favorites'
        )::json
        WHERE (settings::jsonb -> 'compute_configs') ? 'favorites'
        """
    )


def downgrade() -> None:
    """Nothing to put back: the previous code reads a missing list as empty."""
    pass
