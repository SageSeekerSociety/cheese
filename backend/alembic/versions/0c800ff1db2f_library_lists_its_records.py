"""The library lists its records, and each record says where its bytes are

Revision ID: 0c800ff1db2f
Revises: 9f2b7c14a8e3
Create Date: 2026-10-08

The library page listed whatever was under ``.library/<project>/`` on disk and
looked the records up beside it. From here the records table is the list: a
name is a row, a folder is the ``/`` in a name, and the bytes are wherever the
row says (#2114). So:

- ``library_files.location`` and ``library_files.blob_key`` say where a row's
  bytes are. Rows written before this have no key yet; the code derives it from
  the directories they were written to (``records.blob_key``). The key is not
  backfilled here: the release this deploy replaces still serves while this
  runs, and its 「替换」 moves the old bytes into ``.library-history`` — a key
  written now would then point at the new bytes. The next migration fills the
  keys, once nothing writes the old way.
- A file on disk with no current row (one put in before the records table,
  ``b4e1c2d9a7f3``) gets one, or it would drop out of the list. Who gave it and
  in which room come from the first message that attached it, which is where
  the page used to look them up.
- Files the rooms-become-tasks migrations copied out of a room (``<room
  title>/<path in the room>``, ``fc3eab9b8847`` and ``a6715909ab7d``) were
  recorded with nobody as their source, and the page said 「来源未知」. Each was
  saved in its room by someone, and that room's file history (moved to 综合,
  path and content unchanged) says who.
"""

import hashlib
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "0c800ff1db2f"
down_revision: str | Sequence[str] | None = "9f2b7c14a8e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _record_unlisted(workspace: Path) -> None:
    root = workspace / ".library"
    if not root.is_dir():
        return
    bind = op.get_bind()
    projects = {
        str(p) for p in bind.execute(sa.text("SELECT id FROM projects")).scalars()
    }
    for folder in sorted(root.iterdir()):
        if folder.name not in projects or not folder.is_dir():
            continue
        listed = set(
            bind.execute(
                sa.text(
                    "SELECT name FROM library_files"
                    " WHERE project_id = :p AND superseded_at IS NULL"
                ),
                {"p": folder.name},
            ).scalars()
        )
        for path in sorted(folder.rglob("*")):
            name = path.relative_to(folder).as_posix()
            # `.名字.<随机串>` is a replacement caught halfway.
            if not path.is_file() or name in listed:
                continue
            if any(part.startswith(".") for part in name.split("/")):
                continue
            data = path.read_bytes()
            bind.execute(
                sa.text("""
                    INSERT INTO library_files
                        (id, project_id, name, bytes, sha256, location,
                         added_by, room_id, created_at)
                    SELECT :id, :p, :name, :bytes, :sha, 'local',
                           sent.author,
                           (SELECT t.id FROM topics t WHERE t.id = COALESCE(
                               (SELECT k.room_id FROM tasks k
                                 WHERE k.id = sent.conversation_id),
                               (SELECT h.room_id FROM threads h
                                 WHERE h.id = sent.conversation_id),
                               sent.conversation_id)),
                           COALESCE(sent.created_at, :mtime)
                    FROM (SELECT 1) AS one
                    LEFT JOIN LATERAL (
                        SELECT b.author, b.conversation_id, b.created_at
                        FROM blocks b
                        WHERE b.project_id = :p AND b.kind = 'attachment'
                          AND b.content = :ref
                        ORDER BY b.created_at LIMIT 1
                    ) AS sent ON true
                    ON CONFLICT (project_id, name) WHERE superseded_at IS NULL
                    DO NOTHING
                """),
                {
                    "id": uuid.uuid4(),
                    "p": folder.name,
                    "name": name,
                    "bytes": len(data),
                    "sha": hashlib.sha256(data).hexdigest(),
                    "ref": f"library/{name}",
                    "mtime": datetime.fromtimestamp(path.stat().st_mtime, UTC),
                },
            )


def _name_who_saved_room_copies() -> None:
    op.execute("""
        UPDATE library_files l SET added_by = m.author_handle
        FROM (
            SELECT DISTINCT ON (f.id) f.id, r.author_handle
            FROM library_files f
            JOIN room_file_revisions r
              ON r.project_id = f.project_id
             AND r.sha256 = f.sha256
             AND r.path = substring(f.name FROM position('/' IN f.name) + 1)
            WHERE f.added_by IS NULL AND position('/' IN f.name) > 0
            ORDER BY f.id, r.created_at DESC
        ) m
        WHERE l.id = m.id AND l.added_by IS NULL
    """)


def upgrade() -> None:
    with_lock_retries("library_files")
    op.add_column(
        "library_files",
        sa.Column(
            "location", sa.String(length=16), nullable=False, server_default="local"
        ),
    )
    op.add_column(
        "library_files", sa.Column("blob_key", sa.String(length=1024), nullable=True)
    )
    _name_who_saved_room_copies()

    # migration-safety: allow app-import — the workspace is wherever the running backend reads it from (environment and .env); a copied default would scan a different directory and record nothing
    from app.core.config import settings

    _record_unlisted(Path(settings.workspace_root))


def downgrade() -> None:
    op.drop_column("library_files", "blob_key")
    op.drop_column("library_files", "location")
