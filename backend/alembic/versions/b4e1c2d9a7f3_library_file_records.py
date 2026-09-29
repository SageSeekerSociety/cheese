"""Library file records: who added each file, where, and its replaced versions

Revision ID: b4e1c2d9a7f3
Revises: c4f1d8a2e9b7

The library's bytes stay on disk, addressed by name. This table records what the
disk cannot: who added a file, in which room, when, its size and hash, and the
versions a "replace" superseded. Files already on disk get no row here; the
library page finds their source in the first message that referenced them.

## Downgrade

Drops the table. The files on disk are untouched; replaced versions kept under
`.library-history/` are no longer listed.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4e1c2d9a7f3"
down_revision: str | Sequence[str] | None = "c4f1d8a2e9b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "library_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("added_by", sa.String(length=128), nullable=True),
        sa.Column("room_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["room_id"], ["topics.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_library_files_project_id", "library_files", ["project_id"], unique=False
    )
    op.create_index(
        "uq_library_files_current",
        "library_files",
        ["project_id", "name"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_library_files_current", table_name="library_files")
    op.drop_index("ix_library_files_project_id", table_name="library_files")
    op.drop_table("library_files")
