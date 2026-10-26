"""room_file_collab —— a room file edited by several people at once

A room file is addressed by (room, path) and versions by a content hash, so it
has no id for the collaboration service to name. Opening one in a live editor
now leaves a row here and the service's name carries that row's id; the Yjs
state it holds is what the open editors share.

New table only; downgrading drops the live state it holds. The file's own
revisions (`room_file_revisions`) are untouched — they are what a reader sees,
and every store through the service still becomes one of them.

Revision ID: c4d5e6f7a8b9
Revises: 8103084f5e42
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "8103084f5e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "room_file_collab",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey(
                "projects.id",
                ondelete="CASCADE",
                name="fk_room_file_collab_project_id_projects",
            ),
            nullable=False,
        ),
        sa.Column(
            "room_id",
            sa.Uuid(),
            sa.ForeignKey(
                "topics.id",
                ondelete="CASCADE",
                name="fk_room_file_collab_room_id_topics",
            ),
            nullable=False,
        ),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("state", sa.LargeBinary(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("suggestions", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("room_id", "path", name="uq_room_file_collab_room_path"),
    )
    op.create_index(
        "ix_room_file_collab_project_id", "room_file_collab", ["project_id"]
    )
    op.create_index(
        "ix_room_file_collab_room_path", "room_file_collab", ["room_id", "path"]
    )


def downgrade() -> None:
    op.drop_index("ix_room_file_collab_room_path", "room_file_collab")
    op.drop_index("ix_room_file_collab_project_id", "room_file_collab")
    op.drop_table("room_file_collab")
