"""Persist room cleanup deadlines without scheduling historical deletion."""

import sqlalchemy as sa

from alembic import op

revision = "f9d38ab12760"
down_revision = "e8b42a731c90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical data requires an inventory before it receives a deletion deadline.
    op.add_column("topics", sa.Column("cleanup_due_at", sa.DateTime(timezone=True)))
    op.create_index("ix_topics_cleanup_due_at", "topics", ["cleanup_due_at"])
    op.add_column("topics", sa.Column("resource_id", sa.Uuid(), nullable=True))
    op.add_column("topics", sa.Column("cleanup_id", sa.Uuid(), nullable=True))
    op.create_table(
        "room_cleanups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("resources", sa.JSON(), nullable=False),
        sa.Column("last_error", sa.String(2048)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_room_cleanups_topic_id", "room_cleanups", ["topic_id"])
    op.create_index("ix_room_cleanups_due_at", "room_cleanups", ["due_at"])
    op.create_table(
        "raw_transcripts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(1024), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("chunks", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_raw_transcripts_project_id", "raw_transcripts", ["project_id"])
    op.create_index("ix_raw_transcripts_topic_id", "raw_transcripts", ["topic_id"])


def downgrade() -> None:
    op.drop_table("raw_transcripts")
    op.drop_table("room_cleanups")
    op.drop_column("topics", "resource_id")
    op.drop_column("topics", "cleanup_id")
    op.drop_index("ix_topics_cleanup_due_at", table_name="topics")
    op.drop_column("topics", "cleanup_due_at")
