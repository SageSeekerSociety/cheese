"""tasks.title_source: whether a task still carries the unnamed placeholder

A message upgraded into a task opens it unnamed, under the room placeholder
「新话题」. Screens could not tell that from a typed title, so they showed the
Chinese text in every language. The flag says so instead, as
`topics.title_source` does for rooms.

Additive: the previous image neither reads nor writes the column, and the
server default `human` is right for every task it creates. Existing tasks
opened from a message that were never renamed are marked `placeholder`.

Revision ID: f9be82f700e2
Revises: ddf13581d122
"""

import sqlalchemy as sa

from alembic import op

revision = "f9be82f700e2"
down_revision = "ddf13581d122"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "title_source",
            sa.Enum(
                "placeholder",
                "human",
                name="tasktitlesource",
                native_enum=False,
                length=16,
            ),
            nullable=False,
            server_default="human",
        ),
    )
    op.execute(
        "UPDATE tasks SET title_source = 'placeholder' "
        "WHERE upgraded_from_block_id IS NOT NULL AND title = '新话题'"
    )


def downgrade() -> None:
    op.drop_column("tasks", "title_source")
