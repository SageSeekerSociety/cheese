"""A conversation's files and documents stay in it until someone saves them

Revision ID: 5d0e8b3c7a21
Revises: 49a2e86fa493
Create Date: 2026-10-16

Until now every file sent in a conversation went into the project's library,
and every document 芝士 wrote in one was the project's: a private chat's or a
private channel's files and documents were open to the whole project (#3828).
From here a sent file stays in its conversation's files (``uploads/<随机串>/``),
a document 芝士 writes there belongs to that conversation (``documents.room_id``)
until someone saves it to the library (``documents.kept_at``), and a file saved
from a conversation remembers which one it was (``library_files.source_path``).

What is already there is sorted the way the new rule would have put it:

- A document 芝士 made in a public channel stays in the library: it gets its
  channel and is marked saved, so the channel's 「文件」 says so. One made in a
  private chat or a private channel goes back to that conversation. Where a
  document was made is the line its creation left (``meta.doc_created``).
- A file sent in a private chat or a private channel goes back to that
  conversation, unless something outside it refers to it — a message in
  another conversation, a chip, a document — in which case it stays where those
  find it. Its bytes are copied into the conversation's files and the messages
  that carried it now carry the new address. The library's copy of the bytes is
  left on disk: this runs in a transaction that can still roll back, and a
  deleted file cannot.
"""

import uuid
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "5d0e8b3c7a21"
down_revision: str | Sequence[str] | None = "49a2e86fa493"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "fk_documents_room_id_topics"
_INDEX = "ix_documents_room_id"

_ROOM_OF = """
    COALESCE(
        (SELECT room_id FROM tasks WHERE tasks.id = {c}),
        (SELECT room_id FROM threads WHERE threads.id = {c}),
        {c}
    )
"""


def _documents_find_their_conversations() -> None:
    op.execute(
        sa.text(f"""
            WITH made AS (
                SELECT DISTINCT ON (d.id)
                    d.id AS document_id,
                    {_ROOM_OF.format(c="b.conversation_id")} AS room_id
                FROM documents d
                JOIN blocks b
                  ON b.project_id = d.project_id
                 AND b.kind = 'event'
                 AND b.meta->>'doc_created' = 'true'
                 AND b.meta->'document'->>'id' = d.id::text
                WHERE d.number IS NOT NULL
                ORDER BY d.id, b.created_at
            )
            UPDATE documents d
               SET room_id = t.id,
                   kept_at = CASE
                       WHEN t.is_private OR t.members_only THEN NULL
                       ELSE d.created_at
                   END
              FROM made
              JOIN topics t ON t.id = made.room_id
             WHERE d.id = made.document_id
        """)
    )


def _private_files_go_back(workspace: Path) -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(f"""
            SELECT f.project_id, f.room_id, f.name, f.blob_key
              FROM library_files f
              JOIN topics t ON t.id = f.room_id
             WHERE f.superseded_at IS NULL
               AND (t.is_private OR t.members_only)
               AND NOT EXISTS (
                   SELECT 1 FROM blocks b
                    WHERE b.project_id = f.project_id
                      AND b.content = 'library/' || f.name
                      AND {_ROOM_OF.format(c="b.conversation_id")} <> f.room_id
               )
               AND NOT EXISTS (
                   SELECT 1 FROM blocks b
                    WHERE b.project_id = f.project_id
                      AND strpos(b.content, '<&library/' || f.name || '>') > 0
               )
               AND NOT EXISTS (
                   SELECT 1 FROM documents d
                    WHERE d.project_id = f.project_id
                      AND strpos(d.content, 'library/' || f.name) > 0
               )
        """)
    ).all()
    for project_id, room_id, name, blob_key in rows:
        source = workspace / blob_key
        if not source.is_file():
            continue
        path = f"uploads/{uuid.uuid4().hex}/{name.rsplit('/', 1)[-1]}"
        target = workspace / ".room-files" / str(project_id) / str(room_id) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        bind.execute(
            sa.text(f"""
                UPDATE blocks SET content = :path
                 WHERE project_id = :project
                   AND content = :old
                   AND {_ROOM_OF.format(c="conversation_id")} = :room
            """),
            {
                "path": path,
                "project": project_id,
                "old": f"library/{name}",
                "room": room_id,
            },
        )
        bind.execute(
            sa.text(
                "DELETE FROM library_files WHERE project_id = :project AND name = :name"
            ),
            {"project": project_id, "name": name},
        )


def _workspace_root() -> str:
    # migration-safety: allow app-import — the workspace is wherever the running backend reads it from (environment and .env); a copied default would look in a different directory and move nothing
    from app.core.config import settings

    return settings.workspace_root


def upgrade() -> None:
    # The new foreign key locks `topics` as well as `documents`.
    with_lock_retries("documents, topics, library_files")
    op.add_column("documents", sa.Column("room_id", sa.Uuid(), nullable=True))
    op.add_column(
        "documents", sa.Column("kept_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        _CONSTRAINT,
        "documents",
        "topics",
        ["room_id"],
        ["id"],
        ondelete="CASCADE",
        postgresql_not_valid=True,
    )
    op.add_column(
        "library_files",
        sa.Column("source_path", sa.String(length=1024), nullable=True),
    )
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TABLE documents VALIDATE CONSTRAINT {_CONSTRAINT}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
        op.create_index(_INDEX, "documents", ["room_id"], postgresql_concurrently=True)
    _documents_find_their_conversations()
    _private_files_go_back(Path(_workspace_root()))


def downgrade() -> None:
    op.drop_column("library_files", "source_path")
    op.drop_index(_INDEX, table_name="documents")
    op.drop_constraint(_CONSTRAINT, "documents", type_="foreignkey")
    op.drop_column("documents", "kept_at")
    op.drop_column("documents", "room_id")
