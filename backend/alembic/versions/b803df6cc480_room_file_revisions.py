"""room_file_revisions —— a room file's draft history

A room file had one state: the bytes on disk, and a version that was only their
hash. A person editing a report in the room, and 芝士 editing it after them,
overwrote each other with nothing to go back to. Each save now leaves a row,
and its bytes are kept content-addressed beside the room files.

New table only; downgrading drops it and the history it holds.

Revision ID: b803df6cc480
Revises: 9e4a1c3b7d52
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b803df6cc480"
down_revision: str | Sequence[str] | None = "9e4a1c3b7d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "room_file_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "room_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("author_handle", sa.String(64), nullable=False),
        sa.Column("author_kind", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("editor_key", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("room_id", "path", "seq", name="uq_room_file_revision_seq"),
    )
    op.create_index(
        "ix_room_file_revisions_project_id", "room_file_revisions", ["project_id"]
    )
    op.create_index(
        "ix_room_file_revisions_room_path", "room_file_revisions", ["room_id", "path"]
    )


def downgrade() -> None:
    op.drop_index("ix_room_file_revisions_room_path", "room_file_revisions")
    op.drop_index("ix_room_file_revisions_project_id", "room_file_revisions")
    op.drop_table("room_file_revisions")
