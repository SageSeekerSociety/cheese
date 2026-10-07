"""The copies of old room directories kept for thirty days

Revision ID: 7d3e1c4b9a20
Revises: 1ed9ee06ed4a
Create Date: 2026-10-07

A room's cleanup no longer refuses to delete what is not pushed. The rooms from
before rooms had an executor worked in their directory with no repository
behind it, so before one of those is deleted its files go to the private bucket
for thirty days (`agent/device_storage.py`); this table records each home
looked at and where its copy is.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7d3e1c4b9a20"
down_revision: str | Sequence[str] | None = "1ed9ee06ed4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kept_room_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("key", sa.String(length=512), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "resource_id", name="uq_kept_room_files_home"),
    )
    op.create_index("ix_kept_room_files_expires_at", "kept_room_files", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_kept_room_files_expires_at", table_name="kept_room_files")
    op.drop_table("kept_room_files")
