"""agent_instances.name_source: whether an agent still has its default name

A project's first agent is stored as 「芝士」, because agents and prompts read
that name, and screens showed it as 芝士 in every language. The flag tells a
screen the name is the platform's default, which it shows in its reader's
language, as `topics.title_source` does for unnamed rooms.

Additive: the previous image neither reads nor writes the column, and the
server default `human` is right for every name it writes through a rename.
Agents still called 芝士 are marked `default`; a rename was never recorded, so
one a person deliberately named 芝士 is marked the same way.

Revision ID: da05dacf50ae
Revises: b7c41e0a93fd
"""

import sqlalchemy as sa

from alembic import op

revision = "da05dacf50ae"
down_revision = "b7c41e0a93fd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_instances",
        sa.Column(
            "name_source",
            sa.Enum(
                "default",
                "human",
                name="namesource",
                native_enum=False,
                length=16,
            ),
            nullable=False,
            server_default="human",
        ),
    )
    op.execute(
        "UPDATE agent_instances SET name_source = 'default' "
        "WHERE display_name IN ('芝士', '')"
    )


def downgrade() -> None:
    op.drop_column("agent_instances", "name_source")
