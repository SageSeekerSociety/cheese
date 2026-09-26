"""topics.title_source/version/checked_at/calibrated, topic_titles —— the platform names rooms

The platform now names a room itself and may rename it when the room's
direction changes, so it has to know who chose the current title: a title a
person chose is never overwritten. ``title_version`` makes an automatic rename
lose to a person who renamed while it was being generated.

Existing rooms: still 「新话题」 → ``placeholder`` (the platform names them on
their next message); every other title → ``human``. We cannot tell which old
titles a person typed, and the decision (2026-09-26) is that rooms named before
this change are never renamed automatically.

topic_titles keeps every title a room has had, for undo and for finding a room
by an old name.

Revision ID: 7c2e91a4d3f6
Revises: 52b13868b0e8
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7c2e91a4d3f6"
down_revision: str | Sequence[str] | None = "52b13868b0e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "topics",
        sa.Column(
            "title_source",
            sa.Enum(
                "placeholder",
                "auto",
                "human",
                name="titlesource",
                native_enum=False,
                length=16,
            ),
            nullable=False,
            server_default="placeholder",
        ),
    )
    op.add_column(
        "topics",
        sa.Column("title_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "topics",
        sa.Column("title_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "topics",
        sa.Column(
            "title_calibrated", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.execute("UPDATE topics SET title_source = 'human' WHERE title <> '新话题'")

    op.create_table(
        "topic_titles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "placeholder",
                "auto",
                "human",
                name="titlesource",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_topic_titles_topic_created",
        "topic_titles",
        ["topic_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_topic_titles_topic_created", table_name="topic_titles")
    op.drop_table("topic_titles")
    op.drop_column("topics", "title_calibrated")
    op.drop_column("topics", "title_checked_at")
    op.drop_column("topics", "title_version")
    op.drop_column("topics", "title_source")
