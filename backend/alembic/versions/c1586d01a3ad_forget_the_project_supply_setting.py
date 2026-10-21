"""forget the project supply setting

Revision ID: c1586d01a3ad
Revises: 91c99cd9144d
"""

from alembic import op

revision = "c1586d01a3ad"
down_revision = "91c99cd9144d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Which pool serves a turn is a fact about the model it is bound to, and
    # nothing reads `settings["supply"]` any more. A row still holding the key
    # would read as a choice that still means something.
    # `settings` is json, not jsonb, and the key-delete operator is jsonb-only.
    op.execute(
        "UPDATE projects "
        "SET settings = (settings::jsonb - 'supply')::json "
        "WHERE settings::jsonb ? 'supply'"
    )


def downgrade() -> None:
    """Nothing to put back: no code reads the key, and the only value it held
    in use was the default it fell back to without it."""
    pass
