"""Rooms become tasks of the project's general channel

Revision ID: fc3eab9b8847
Revises: b6fcc6362b79
Create Date: 2026-10-05

A project keeps one place where people talk: its root room, retitled 综合
(general). Every other shared room was, in practice, one person working one
thing with the AI teammate (#2422 ③), so it becomes a task in 综合 under the
same id. Its conversation, its sessions and every row already keyed by
``conversation_id`` stay where they are; only ``conversations.kind`` changes.

Which rooms, and who owns them:

- **Private rooms** (1:1 chats) are not touched.
- **A room nobody has said anything in** — no message from a person, no task,
  no machine home — is deleted with its conversation.
- **An archived room whose machine cleanup has not finished** (pending,
  preparing, retained) stays an archived room: the cleanup finds what it
  removes through the room's own row.
- **Every other room becomes a task.** Its owner is the person who said the
  most in it over the last 60 days, else ever, else whoever created it if that
  is a person, else the project's owner. The other people who spoke in it in
  those 60 days become the task's collaborators. Its AI teammate is the one
  whose session ran in it last. An active room becomes an open, started task;
  an archived one a closed task, closed when it was archived.

What a room had moves with it, or to 综合:

- **To the task:** its living document (``tasks.document_id``), its work
  computer choice (``topics.compute_config``, or the device ``device_topic``
  pinned), its accept cards (``task_id``), the messages it was upgraded from
  (``blocks.upgraded_to_task_id``).
- **To 综合:** the tasks it held (tasks do not nest), the rooms nested in it,
  its machine homes and the session leases on them (their room generation
  becomes 综合's, so a session keeps its machine), its dispatch ledger,
  notifications, feedback, library file origins, skills, routines, proposals,
  mail drafts, memory passes, and the people and AI seats of an active room's
  roster.
- **Kept as they are:** read cursors. A cursor now belongs to a conversation
  (``topic_read_states.topic_id`` references ``conversations``), so a
  person who had read the room has read the task, and only what is said
  after counts as unread.
- **Gone with the room:** title history, room locks, dismissed feedback
  proposals, finished cleanup records, its device pin and its roster.

A session that was running in a converted room is stopped: its process
answers to the room's generation, which is now 综合's. Its turn ends, its
process location is cleared (the orphaned screen is retired as after a room
reopens), and its next message starts it again on the same machine.

Room files live on disk under ``.room-files/<project>/<room>``. Pasted
images (``uploads/``, unique paths that old messages reference) are copied
into 综合's area under the same paths. Everything else a room kept is copied
into the project library under a folder named after the room. A room area
marked ``.imported-task-delivery`` holds a whole copy of an old worktree and
is left where it is. Sources are not deleted.
"""

import hashlib
import re
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision: str = "fc3eab9b8847"
down_revision: str | Sequence[str] | None = "b6fcc6362b79"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GENERAL = "综合"
RECENT = "60 days"
UNFINISHED_CLEANUP = ("pending", "preparing", "retained")
IMPORTED_MARKER = ".imported-task-delivery"


def _lock(tables: str) -> None:
    """As in b6fcc6362b79: queue for every table, a few seconds at a time."""
    op.execute(f"""
        DO $$
        DECLARE
            attempts integer := 0;
            outer_timeout text := current_setting('lock_timeout');
        BEGIN
            PERFORM set_config('lock_timeout', '3s', true);
            LOOP
                BEGIN
                    LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE;
                    EXIT;
                EXCEPTION WHEN lock_not_available OR deadlock_detected THEN
                    attempts := attempts + 1;
                    IF attempts >= 100 THEN
                        RAISE;
                    END IF;
                    PERFORM pg_sleep(0.2);
                END;
            END LOOP;
            PERFORM set_config('lock_timeout', outer_timeout, true);
        END
        $$
    """)


def _cursors_follow_conversations() -> None:
    """A read cursor is a person's place in one conversation, a room's or a
    task's. Pointed at ``conversations`` before any room row goes, so the
    cursors of converted rooms survive their ``topics`` row."""
    op.execute(
        "ALTER TABLE topic_read_states DROP CONSTRAINT topic_read_states_topic_id_fkey"
    )
    op.execute(
        "ALTER TABLE topic_read_states ADD CONSTRAINT topic_read_states_topic_id_fkey"
        " FOREIGN KEY (topic_id) REFERENCES conversations (id) ON DELETE CASCADE"
    )


def _choose() -> None:
    """``room_fate``: every shared room that is not a root, and what becomes
    of it. A username is unique only among live accounts, so a deleted
    account can share one with a live account; a handle is a person once."""
    unfinished = ", ".join(f"'{state}'" for state in UNFINISHED_CLEANUP)
    op.execute("""
        CREATE TEMP TABLE people ON COMMIT DROP AS
        SELECT DISTINCT u.username AS handle FROM "user" u
        WHERE NOT EXISTS (SELECT 1 FROM agent_bindings b WHERE b.user_id = u.id)
    """)
    op.execute("CREATE UNIQUE INDEX ON people (handle)")
    op.execute(f"""
        CREATE TEMP TABLE room_fate ON COMMIT DROP AS
        SELECT t.id, t.project_id, p.root_topic_id AS general_id,
               COALESCE(g.resource_id, g.id)::text AS general_resource,
               COALESCE(t.resource_id, t.id)::text AS resource,
               t.title, t.status = 'archived' AS archived, t.archived_at,
               t.created_by, p.owner_handle AS project_owner,
               'convert'::text AS fate
        FROM topics t
        JOIN projects p ON p.id = t.project_id
        JOIN topics g ON g.id = p.root_topic_id
        WHERE t.id <> p.root_topic_id
          AND NOT t.is_private
          AND NOT EXISTS (
              SELECT 1 FROM room_cleanups c
              WHERE c.topic_id = t.id AND c.state IN ({unfinished})
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
    # Every conversation a converted room answers for: its own and its tasks'.
    op.execute("""
        CREATE TEMP TABLE converted_conversations ON COMMIT DROP AS
        SELECT id, general_id, general_resource, resource FROM converted
        UNION ALL
        SELECT k.id, c.general_id, c.general_resource, c.resource
        FROM tasks k JOIN converted c ON c.id = k.room_id
    """)
    op.execute("CREATE UNIQUE INDEX ON converted_conversations (id)")


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


def _stop_sessions() -> None:
    op.execute("""
        UPDATE agent_turns SET stopped_at = now()
        WHERE stopped_at IS NULL
          AND conversation_id IN (SELECT id FROM converted_conversations)
    """)
    op.execute("""
        UPDATE agent_sessions s SET
            runtime_location = NULL,
            work_lease = CASE
                WHEN s.work_lease IS NULL THEN NULL
                WHEN s.work_lease::jsonb ->> 'room_resource_id' = c.resource
                THEN (s.work_lease::jsonb || jsonb_build_object(
                    'room_resource_id', c.general_resource))::json
                ELSE s.work_lease
            END
        FROM converted_conversations c
        WHERE s.conversation_id = c.id
    """)
    op.execute("""
        UPDATE cloud_host_homes h SET
            topic_id = c.general_id,
            room_resource_id = c.general_resource
        FROM converted c WHERE h.topic_id = c.id
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
    op.execute("""
        INSERT INTO topic_memberships
            (id, topic_id, member_handle, role, created_at, updated_at)
        SELECT gen_random_uuid(), c.general_id, m.member_handle, 'member',
               now(), now()
        FROM topic_memberships m JOIN converted c ON c.id = m.topic_id
        WHERE NOT c.archived
        ON CONFLICT (topic_id, member_handle) DO NOTHING
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
            c.id, c.project_id, c.general_id, left(t.title, 300),
            CASE WHEN t.title_source = 'placeholder' THEN 'placeholder'
                 ELSE 'human' END,
            CASE WHEN c.archived THEN 'closed' ELSE 'open' END,
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
            CASE WHEN c.archived THEN COALESCE(c.archived_at, t.updated_at) END,
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
    # Neither holds a foreign key to the room, so neither goes with it.
    for table in ("room_cleanups", "device_topic"):
        op.execute(f"""
            DELETE FROM {table} x USING room_fate c WHERE x.topic_id = c.id
        """)  # noqa: S608
    op.execute("""
        DELETE FROM topics t USING converted c WHERE t.id = c.id
    """)
    op.execute("""
        UPDATE conversations SET kind = 'task'
        WHERE id IN (SELECT id FROM converted)
    """)
    op.execute(f"""
        UPDATE topics SET title = '{GENERAL}', title_source = 'human'
        WHERE id IN (SELECT root_topic_id FROM projects)
    """)


def _folder_name(title: str, taken: set[str]) -> str:
    name = re.sub(r"[\x00-\x1f\x7f/\\]", " ", title).strip().strip(".")
    name = (name or "未命名")[:80]
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name} ({n})", n + 1
    taken.add(candidate)
    return candidate


def _copy_files(workspace: Path) -> None:
    """Copy each converted room's files: pasted images to 综合's area, the rest
    into the project library. Re-running finds them already there."""
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
    _lock("topics, tasks, conversations, topic_read_states")
    _cursors_follow_conversations()
    _choose()
    _delete_the_empty()
    op.execute("ALTER TABLE tasks DISABLE TRIGGER tasks_conversation_registered")
    op.execute("ALTER TABLE topics DISABLE TRIGGER topics_conversation_unregistered")
    _stop_sessions()
    _move_to_general()
    _make_tasks()
    _retire_rooms()
    op.execute("ALTER TABLE tasks ENABLE TRIGGER tasks_conversation_registered")
    op.execute("ALTER TABLE topics ENABLE TRIGGER topics_conversation_unregistered")

    from app.core.config import settings

    _copy_files(Path(settings.workspace_root))


def downgrade() -> None:
    raise RuntimeError(
        "Rooms made tasks cannot be told apart from tasks afterwards; restore a"
        " verified backup instead."
    )
