"""The rooms left archived become closed tasks; no channel has a document

Revision ID: a6715909ab7d
Revises: b672fdeb358e
Create Date: 2026-10-07

fc3eab9b8847 made every shared room a task of its project's 综合, except the
archived rooms whose machine cleanup had not finished: the cleanup found what
it removes through the room's row. It does not any more (`topic/retire.py`
reads the cleanup's own project, generation and resources), so those rooms
become closed tasks here by the same rules, closed when they were archived.

Which rooms: archived, shared, not a project's 综合, and left by that
migration. On dev, channels have been made and archived since; they were
created after it ran (``CONVERTED_AT``), and stay. A database that has never
had a 支线 opened in it (``threads`` empty) is one where nothing has been used
as a channel yet, as on a deployment that runs both migrations at once, and
there every archived room is one that was left.

What differs from fc3eab9b8847, because the cleanup is still to run:

- **Machine homes** (``cloud_host_homes``) stay with the room's id, which is
  now its task's conversation: the column references ``conversations``. Their
  generation is unchanged, and so are the leases of the room's sessions.
- **Cleanup records and device pins** (``room_cleanups``, ``device_topic``)
  are kept: the cleanup reads them.
- Nobody joins 综合's roster: an archived room's people had left it.

Everything else follows fc3eab9b8847: who owns the task and who helped, the
document, the cards and the room's own tasks; the rooms nobody spoke in are
deleted; files are copied to 综合 and the library.

After it, no channel and no private chat has a living document, so
``documents.room_id`` goes. One still kept by a channel archived since, or by
a private chat, becomes a document of the project's own under its title when
it was written, and is deleted when it was not, as 53267de872b8 did for the
rest.
"""

import hashlib
import re
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "a6715909ab7d"
down_revision: str | Sequence[str] | None = "b672fdeb358e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: When fc3eab9b8847 reached main. Dev ran it minutes later; every room it
#: left had been made before.
CONVERTED_AT = "2026-10-05T17:26:33+00:00"
RECENT = "60 days"
IMPORTED_MARKER = ".imported-task-delivery"


def _homes_follow_conversations() -> None:
    """Before any room row goes: a home outlives its room's row as long as
    the room's conversation is there."""
    op.execute(
        "ALTER TABLE cloud_host_homes DROP CONSTRAINT cloud_host_homes_topic_id_fkey"
    )
    op.execute(
        "ALTER TABLE cloud_host_homes ADD CONSTRAINT cloud_host_homes_topic_id_fkey"
        " FOREIGN KEY (topic_id) REFERENCES conversations (id) ON DELETE CASCADE"
    )


def _choose() -> None:
    op.execute("""
        CREATE TEMP TABLE people ON COMMIT DROP AS
        SELECT DISTINCT u.username AS handle FROM "user" u
        WHERE NOT EXISTS (SELECT 1 FROM agent_bindings b WHERE b.user_id = u.id)
    """)
    op.execute("CREATE UNIQUE INDEX ON people (handle)")
    op.execute(f"""
        CREATE TEMP TABLE room_fate ON COMMIT DROP AS
        SELECT t.id, t.project_id, p.root_topic_id AS general_id,
               t.title, t.archived_at, t.updated_at, t.created_by,
               p.owner_handle AS project_owner, 'convert'::text AS fate
        FROM topics t
        JOIN projects p ON p.id = t.project_id
        WHERE t.id <> p.root_topic_id
          AND NOT t.is_private
          AND t.status = 'archived'
          AND (
              t.created_at < '{CONVERTED_AT}'::timestamptz
              OR NOT EXISTS (SELECT 1 FROM threads)
          )
    """)
    op.execute("CREATE UNIQUE INDEX ON room_fate (id)")
    op.execute(f"""
        CREATE TEMP TABLE said ON COMMIT DROP AS
        SELECT b.conversation_id AS room, b.author,
               count(*) AS lines,
               count(*) FILTER (
                   WHERE b.created_at > now() - interval '{RECENT}'
               ) AS recent,
               max(b.created_at) AS last_at
        FROM blocks b
        JOIN room_fate r ON r.id = b.conversation_id
        JOIN people pe ON pe.handle = b.author
        WHERE b.kind = 'message'
        GROUP BY b.conversation_id, b.author
    """)
    op.execute("""
        UPDATE room_fate r SET fate = 'delete'
        WHERE NOT EXISTS (SELECT 1 FROM said s WHERE s.room = r.id)
          AND NOT EXISTS (SELECT 1 FROM tasks k WHERE k.room_id = r.id)
          AND NOT EXISTS (SELECT 1 FROM cloud_host_homes h WHERE h.topic_id = r.id)
    """)
    op.execute("ALTER TABLE room_fate ADD COLUMN owner text")
    op.execute("""
        UPDATE room_fate r SET owner = COALESCE(
            (SELECT s.author FROM said s WHERE s.room = r.id
             ORDER BY s.recent DESC, s.lines DESC, s.last_at DESC, s.author
             LIMIT 1),
            (SELECT pe.handle FROM people pe WHERE pe.handle = r.created_by),
            r.project_owner
        )
        WHERE r.fate = 'convert'
    """)
    op.execute("""
        CREATE TEMP TABLE converted ON COMMIT DROP AS
        SELECT * FROM room_fate WHERE fate = 'convert'
    """)
    op.execute("CREATE UNIQUE INDEX ON converted (id)")


def _delete_the_empty() -> None:
    """With the registration triggers on: the conversation goes with the room."""
    op.execute("""
        UPDATE topics t SET parent_id = f.general_id
        FROM room_fate f WHERE t.parent_id = f.id
    """)
    op.execute("""
        DELETE FROM topics t USING room_fate f
        WHERE t.id = f.id AND f.fate = 'delete'
    """)


def _move_to_general() -> None:
    for table, column in (
        ("tasks", "room_id"),
        ("dispatches", "place_id"),
        ("notification", "topic_id"),
        ("feedback", "topic_id"),
        ("library_files", "room_id"),
        ("memory_dreams", "topic_id"),
        ("project_skills", "source_topic_id"),
        ("routines", "topic_id"),
        ("task_proposals", "room_id"),
        ("compute_runs", "topic_id"),
        ("mail_drafts", "topic_id"),
    ):
        op.execute(f"""
            UPDATE {table} x SET {column} = c.general_id
            FROM converted c WHERE x.{column} = c.id
        """)  # noqa: S608
    op.execute("""
        UPDATE room_file_revisions x SET room_id = c.general_id
        FROM converted c WHERE x.room_id = c.id AND x.path LIKE 'uploads/%'
    """)


def _make_tasks() -> None:
    op.execute("""
        INSERT INTO tasks (
            id, project_id, room_id, title, title_source, status,
            owner_handle, created_by, contributor_handles, agent_handle,
            compute_config, document_id, accepted_by, accepted_at,
            upgraded_from_block_id, closed_at, started_at, started_by,
            started_doc_version, created_at, updated_at
        )
        SELECT
            c.id, c.project_id, c.general_id, left(t.title, 300), 'human',
            'closed',
            c.owner, t.created_by,
            COALESCE((
                SELECT json_agg(s.author ORDER BY s.recent DESC, s.author)
                FROM said s
                WHERE s.room = c.id AND s.recent > 0 AND s.author <> c.owner
            ), '[]'::json),
            (SELECT a.agent_handle FROM agent_sessions a
             WHERE a.conversation_id = c.id
             ORDER BY a.updated_at DESC LIMIT 1),
            COALESCE(t.compute_config, (
                SELECT json_build_object('profile', 'device',
                                         'device_id', d.device_id)
                FROM device_topic d WHERE d.topic_id = c.id
            )),
            doc.id, t.accepted_by, t.accepted_at, t.upgraded_from_block_id,
            COALESCE(c.archived_at, c.updated_at),
            t.created_at, c.owner, doc.version,
            t.created_at, t.updated_at
        FROM converted c
        JOIN topics t ON t.id = c.id
        LEFT JOIN documents doc ON doc.room_id = c.id
    """)
    op.execute("""
        UPDATE accept_cards x SET
            task_id = COALESCE(x.task_id, c.id),
            topic_id = c.general_id
        FROM converted c WHERE x.topic_id = c.id
    """)
    op.execute("""
        UPDATE blocks x SET
            upgraded_to_task_id = x.upgraded_to_topic_id,
            upgraded_to_topic_id = NULL
        FROM converted c WHERE x.upgraded_to_topic_id = c.id
    """)
    op.execute("""
        UPDATE documents d SET room_id = NULL
        FROM converted c WHERE d.room_id = c.id
    """)


def _retire_rooms() -> None:
    op.execute("""
        DELETE FROM topics t USING converted c WHERE t.id = c.id
    """)
    op.execute("""
        UPDATE conversations SET kind = 'task'
        WHERE id IN (SELECT id FROM converted)
    """)


def _no_room_keeps_a_document() -> None:
    op.execute("""
        UPDATE documents d SET title = left(coalesce(t.title, ''), 200), room_id = NULL
        FROM topics t
        WHERE d.room_id = t.id AND btrim(d.content) <> ''
    """)
    op.execute("DELETE FROM documents WHERE room_id IS NOT NULL")
    op.drop_column("documents", "room_id")


def _folder_name(title: str, taken: set[str]) -> str:
    name = re.sub(r"[\x00-\x1f\x7f/\\]", " ", title).strip().strip(".")
    name = (name or "未命名")[:80]
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name} ({n})", n + 1
    taken.add(candidate)
    return candidate


def _copy_files(workspace: Path) -> None:
    """As in fc3eab9b8847: pasted images to 综合's area, the rest into the
    project library. Re-running finds them already there."""
    bind = op.get_bind()
    rooms = bind.execute(
        sa.text(
            "SELECT id, project_id, general_id, title FROM converted"
            " ORDER BY project_id, title, id"
        )
    ).all()
    library_names: dict[str, set[str]] = {}
    for room_id, project_id, general_id, title in rooms:
        source = workspace / ".room-files" / str(project_id) / str(room_id)
        if not source.is_dir() or (source / IMPORTED_MARKER).exists():
            continue
        files = sorted(p for p in source.rglob("*") if p.is_file())
        if not files:
            continue
        uploads = [p for p in files if p.relative_to(source).parts[0] == "uploads"]
        kept = [p for p in files if p not in uploads]
        general = workspace / ".room-files" / str(project_id) / str(general_id)
        for path in uploads:
            target = general / path.relative_to(source)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        if not kept:
            continue
        library = workspace / ".library" / str(project_id)
        taken = library_names.setdefault(
            str(project_id),
            {p.name for p in library.iterdir()} if library.is_dir() else set(),
        )
        folder = _folder_name(title, taken)
        for path in kept:
            name = f"{folder}/{path.relative_to(source).as_posix()}"
            target = library / name
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            data = target.read_bytes()
            bind.execute(
                sa.text(
                    "INSERT INTO library_files (id, project_id, name, bytes,"
                    " sha256, added_by, room_id, created_at)"
                    " VALUES (:id, :project, :name, :bytes, :sha, NULL,"
                    " :general, now())"
                    " ON CONFLICT DO NOTHING"
                ),
                {
                    "id": uuid.uuid4(),
                    "project": project_id,
                    "name": name,
                    "bytes": len(data),
                    "sha": hashlib.sha256(data).hexdigest(),
                    "general": general_id,
                },
            )


def upgrade() -> None:
    with_lock_retries("topics, tasks, conversations, cloud_host_homes, documents")
    _homes_follow_conversations()
    _choose()
    _delete_the_empty()
    op.execute("ALTER TABLE tasks DISABLE TRIGGER tasks_conversation_registered")
    op.execute("ALTER TABLE topics DISABLE TRIGGER topics_conversation_unregistered")
    _move_to_general()
    _make_tasks()
    _retire_rooms()
    op.execute("ALTER TABLE tasks ENABLE TRIGGER tasks_conversation_registered")
    op.execute("ALTER TABLE topics ENABLE TRIGGER topics_conversation_unregistered")
    _no_room_keeps_a_document()

    from app.core.config import settings

    _copy_files(Path(settings.workspace_root))


def downgrade() -> None:
    raise RuntimeError(
        "Rooms made tasks cannot be told apart from tasks afterwards; restore a"
        " verified backup instead."
    )
