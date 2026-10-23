"""Comments can hang on a room file, not only on a document

A comment thread was a row of `document_comments`, under a document, and the
words it was about lived as a mark in that document's shared text. A room file
has no shared text to carry a mark: it is bytes, saved version by version. So a
thread on a file keeps where it points in `anchor` — the place in it (a page, a
cell, a passage, an element) and the version it was read at — and hangs on
`room_id` + `room_path` instead of `document_id`.

Exactly one of the two subjects is set: a thread on a document keeps its
`document_id` and no room file, a thread on a file the reverse. Threads,
replies, replies' numbering and the thread's state and revision are unchanged:
they are the same rows and the same `document_threads`.

`comment_operations` remembers a file thread's writes by operation id, the way
`document_operations` does for a document's, so a retry after an unknown
outcome replays its receipt instead of writing twice.

The columns and constraints go onto the live table without scanning it:
`NOT VALID` first, then `VALIDATE` outside the transaction that added them, and
the index is built `CONCURRENTLY`. The previous release still serving does not
map the new columns and is unaffected.

Revision ID: b7f3c1a90e42
Revises: d2f7a4c8e913
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b7f3c1a90e42"
down_revision: str | Sequence[str] | None = "d2f7a4c8e913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_comments", sa.Column("room_id", sa.Uuid(), nullable=True))
    op.add_column(
        "document_comments",
        sa.Column("room_path", sa.String(512), nullable=True),
    )
    op.add_column(
        "document_comments",
        sa.Column("anchor", postgresql.JSONB(), nullable=True),
    )
    # A thread on a file hangs on no document; the column had been NOT NULL.
    op.alter_column(
        "document_comments",
        "document_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )

    op.execute(
        "ALTER TABLE document_comments ADD CONSTRAINT "
        "fk_document_comments_room_id_topics FOREIGN KEY (room_id) "
        "REFERENCES topics(id) ON DELETE CASCADE NOT VALID"
    )
    op.execute(
        "ALTER TABLE document_comments ADD CONSTRAINT "
        "ck_document_comment_subject CHECK "
        "((document_id IS NULL) <> (room_id IS NULL)) NOT VALID"
    )
    op.execute(
        "ALTER TABLE document_comments ADD CONSTRAINT "
        "ck_document_comment_file_path CHECK "
        "(room_id IS NULL OR room_path IS NOT NULL) NOT VALID"
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_document_comments_room_path "
            "ON document_comments (room_id, room_path)"
        )
        op.execute(
            "ALTER TABLE document_comments VALIDATE CONSTRAINT "
            "fk_document_comments_room_id_topics"
        )
        op.execute(
            "ALTER TABLE document_comments VALIDATE CONSTRAINT "
            "ck_document_comment_subject"
        )
        op.execute(
            "ALTER TABLE document_comments VALIDATE CONSTRAINT "
            "ck_document_comment_file_path"
        )

    op.create_table(
        "comment_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("room_path", sa.String(512), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("receipt", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["topics.id"],
            name="fk_comment_operations_room_id_topics",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "room_id",
            "room_path",
            "actor",
            "action",
            "operation_id",
            name="uq_comment_operation",
        ),
    )
    op.create_index(
        "ix_comment_operations_room_path",
        "comment_operations",
        ["room_id", "room_path"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "irreversible: a file's threads would lose their subject and anchor. "
        "Restore from a backup taken before b7f3c1a90e42 if needed."
    )
