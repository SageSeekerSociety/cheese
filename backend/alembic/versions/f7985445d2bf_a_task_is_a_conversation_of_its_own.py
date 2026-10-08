"""A task is a conversation of its own

Until now a session belonged to a room (`agent_sessions.topic_id`), and a task
was worked by a subagent inside the room's session. A task now has a session of
its own, so a session belongs to a conversation, and a conversation is either a
room or a task.

- **`conversations`** registers every conversation once: its id, its project and
  its kind (`room` or `task`). The id is the room's or the task's own id, so no
  existing row changes id. The registry is kept by triggers, not by the
  application: inserting a room or a task registers it, deleting one (by hand or
  by cascade) removes it. Every room and task that exists is registered here.
- **`agent_sessions` gains `conversation_id`**, pointing at the registry,
  unique per (conversation, agent, harness). A room's sessions keep their row;
  the value is the room's id, which is its conversation id. `topic_id` stays as
  the room the session works in (a task's session works in the task's room).
  Its unused `task_id` column goes, with the two partial indexes on it.
- **`tasks`** gains its living document (`document_id`), the agent working it
  (`agent_handle`), its own work computer choice (`compute_config`, empty means
  the room's), and the record of being started (`started_at`, `started_by`,
  `started_doc_version`). It loses what only a subagent wrote (`subagent_id`,
  `execution_agent_instance_id`, `execution_parent_session_id`,
  `execution_turn_id`, `last_turn_at`) and `brief`.
- **A task never shares an id with a room.** The closed tasks an older
  migration made from rooms' work trees kept their room's id; each gets a new
  one, and every row that points at it follows.
- **Every existing task is started**, at the moment it was created: until now a
  task was handed to a worker when it was opened.
- **A task's brief becomes its document.** Each task with a non-empty brief gets
  a document holding that text as version 1, with that version in its history.
  Its node tree is built the first time the document is stored, as for any
  document whose nodes were never written.
- Task search indexes `title` and `conclusion`; `brief` is gone from it.
- **`task_proposals`**: a task an AI teammate proposes, until a person creates
  it or puts it aside.

Open tasks keep working: the next message to one starts its session, which
continues from the task branch on the forge.

The backend this deploy replaces is still serving while this runs. Every table
this migration changes or adds a foreign key to is locked up front, all at once
or not at all (`with_lock_retries(..., nowait=True)`).

Revision ID: f7985445d2bf
Revises: 6d0ce0a4287b
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "f7985445d2bf"
down_revision: str | Sequence[str] | None = "6d0ce0a4287b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _text_fields(texts: Sequence[str], keywords: Sequence[str]) -> str:
    # As in 2d2fc3a8ce36. A space after every ':' — `op.execute` wraps the
    # string in `text()`, which reads `:word` as a bind parameter.
    fields = []
    for column in texts:
        fields.append(
            f'"{column}": {{"tokenizer": {{"type": "jieba"}}, "record": "position"}}'
        )
        fields.append(
            f'"{column}_ngram": {{"column": "{column}", "tokenizer": '
            '{"type": "ngram", "min_gram": 2, "max_gram": 3, "prefix_only": false}}'
        )
    for column in keywords:
        fields.append(f'"{column}": {{"tokenizer": {{"type": "keyword"}}}}')
    return "{" + ", ".join(fields) + "}"


# Moves each task in `task_new_ids` to its new id. Every foreign key to `tasks`
# is made deferrable for the moment the referencing rows and the task move
# together, then checked and made immediate again; the three columns that hold
# a task id without a foreign key follow by hand.
RENUMBER_TASKS = """
DO $$
DECLARE
    fk record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM task_new_ids) THEN
        RETURN;
    END IF;
    FOR fk IN
        SELECT c.conname, c.conrelid::regclass AS tbl, a.attname AS col
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
        WHERE c.contype = 'f' AND c.confrelid = 'tasks'::regclass
    LOOP
        EXECUTE format(
            'ALTER TABLE %s ALTER CONSTRAINT %I DEFERRABLE INITIALLY DEFERRED',
            fk.tbl, fk.conname
        );
    END LOOP;
    -- A second pass: a table with a pending check can no longer be altered.
    FOR fk IN
        SELECT c.conrelid::regclass AS tbl, a.attname AS col
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
        WHERE c.contype = 'f' AND c.confrelid = 'tasks'::regclass
    LOOP
        EXECUTE format(
            'UPDATE %s x SET %I = m.new_id FROM task_new_ids m WHERE x.%I = m.old_id',
            fk.tbl, fk.col, fk.col
        );
    END LOOP;
    UPDATE tasks t SET id = m.new_id FROM task_new_ids m WHERE t.id = m.old_id;
    UPDATE deliveries x SET task_id = m.new_id
        FROM task_new_ids m WHERE x.task_id = m.old_id;
    UPDATE local_fs_access x SET task_id = m.new_id
        FROM task_new_ids m WHERE x.task_id = m.old_id;
    UPDATE accept_cards c SET delivered_task_ids = (
        SELECT json_agg(COALESCE(m.new_id::text, e.id) ORDER BY e.n)
        FROM json_array_elements_text(c.delivered_task_ids) WITH ORDINALITY e(id, n)
        LEFT JOIN task_new_ids m ON m.old_id::text = e.id
    )
    WHERE json_typeof(c.delivered_task_ids) = 'array'
      AND EXISTS (
        SELECT 1 FROM json_array_elements_text(c.delivered_task_ids) e(id)
        JOIN task_new_ids m ON m.old_id::text = e.id
      );
    SET CONSTRAINTS ALL IMMEDIATE;
    FOR fk IN
        SELECT c.conname, c.conrelid::regclass AS tbl
        FROM pg_constraint c
        WHERE c.contype = 'f' AND c.confrelid = 'tasks'::regclass
    LOOP
        EXECUTE format(
            'ALTER TABLE %s ALTER CONSTRAINT %I NOT DEFERRABLE', fk.tbl, fk.conname
        );
    END LOOP;
END
$$
"""


def upgrade() -> None:
    with_lock_retries(
        "topics, projects, tasks, agent_sessions, documents, document_versions",
        nowait=True,
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("kind IN ('room', 'task')", name="ck_conversations_kind"),
    )
    op.create_index("ix_conversations_project_id", "conversations", ["project_id"])
    # b8e2f4a90d33 gave every room of its day a work tree under the room's own
    # id, and d7a419be028c kept each tree's id for the closed task it became:
    # those tasks share their room's id. A conversation id names one thing, so
    # each takes a new id and everything that points at it follows.
    op.execute("""
        CREATE TEMPORARY TABLE task_new_ids ON COMMIT DROP AS
        SELECT t.id AS old_id, gen_random_uuid() AS new_id
        FROM tasks t JOIN topics r ON r.id = t.id
    """)
    op.execute(RENUMBER_TASKS)
    op.execute("""
        INSERT INTO conversations (id, project_id, kind, created_at)
        SELECT id, project_id, 'room', created_at FROM topics
    """)
    op.execute("""
        INSERT INTO conversations (id, project_id, kind, created_at)
        SELECT id, project_id, 'task', created_at FROM tasks
    """)
    op.execute("""
        CREATE FUNCTION conversation_registered() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO conversations (id, project_id, kind)
            VALUES (NEW.id, NEW.project_id, TG_ARGV[0]);
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE FUNCTION conversation_unregistered() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            DELETE FROM conversations WHERE id = OLD.id;
            RETURN OLD;
        END;
        $$
    """)
    for table, kind in (("topics", "room"), ("tasks", "task")):
        op.execute(f"""
            CREATE TRIGGER {table}_conversation_registered
            BEFORE INSERT ON {table}
            FOR EACH ROW EXECUTE FUNCTION conversation_registered('{kind}')
        """)
        op.execute(f"""
            CREATE TRIGGER {table}_conversation_unregistered
            AFTER DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION conversation_unregistered()
        """)

    # A session belongs to a conversation. `topic_id` stays: it is the room
    # the session works in — its own room, or its task's — which is what every
    # room-wide question about sessions (the room's machine, its switch, its
    # cleanup) asks, and what the device connection owner, which outlives this
    # deploy, reads on every executor call.
    op.add_column(
        "agent_sessions", sa.Column("conversation_id", sa.Uuid(), nullable=True)
    )
    op.execute("UPDATE agent_sessions SET conversation_id = topic_id")
    op.alter_column("agent_sessions", "conversation_id", nullable=False)
    op.create_foreign_key(
        "fk_agent_sessions_conversation_id",
        "agent_sessions",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_index("uq_agent_sessions_room", table_name="agent_sessions")
    # `agent_sessions.task_id` is what a task's session was going to be keyed by
    # before tasks were worked by subagents; nothing has read or written it
    # since, and its two partial indexes would refuse a task's session.
    op.drop_index("uq_agent_sessions_room_task_null", table_name="agent_sessions")
    op.drop_index("uq_agent_sessions_thread", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_task_id", table_name="agent_sessions")
    op.drop_column("agent_sessions", "task_id")
    op.create_index(
        "uq_agent_sessions_conversation",
        "agent_sessions",
        ["conversation_id", "agent_handle", "harness"],
        unique=True,
    )

    # A task's own document, agent, work computer and start.
    op.add_column(
        "tasks",
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey(
                "documents.id", ondelete="SET NULL", name="fk_tasks_document_id"
            ),
            nullable=True,
        ),
    )
    op.add_column("tasks", sa.Column("agent_handle", sa.String(64), nullable=True))
    op.add_column("tasks", sa.Column("compute_config", sa.JSON(), nullable=True))
    op.add_column(
        "tasks", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("tasks", sa.Column("started_by", sa.String(64), nullable=True))
    op.add_column(
        "tasks", sa.Column("started_doc_version", sa.Integer(), nullable=True)
    )

    # The brief becomes the task's document: version 1, in its history.
    op.execute("""
        CREATE TEMPORARY TABLE task_briefs ON COMMIT DROP AS
        SELECT t.id AS task_id, gen_random_uuid() AS document_id, t.project_id,
               t.brief, COALESCE(t.created_by, 'system') AS author, t.created_at
        FROM tasks t
        WHERE btrim(t.brief) <> ''
    """)
    op.execute("""
        INSERT INTO documents
            (id, project_id, room_id, kind, title, content, version, author,
             created_at, updated_at)
        SELECT document_id, project_id, NULL, 'doc', NULL, brief, 1, author,
               created_at, created_at
        FROM task_briefs
    """)
    op.execute("""
        INSERT INTO document_versions
            (id, document_id, version, content, content_hash, previous_version,
             base_version, actor, created_at)
        SELECT gen_random_uuid(), document_id, 1, brief,
               encode(sha256(convert_to(brief, 'UTF8')), 'hex'), NULL, NULL,
               author, created_at
        FROM task_briefs
    """)
    op.execute("""
        UPDATE tasks SET document_id = b.document_id
        FROM task_briefs b WHERE tasks.id = b.task_id
    """)
    # Every task that exists was handed to a worker the moment it was opened:
    # it is started, at the moment it was created, on its brief.
    op.execute("""
        UPDATE tasks SET
            started_at = created_at,
            started_by = COALESCE(created_by, owner_handle),
            started_doc_version = CASE WHEN document_id IS NULL THEN 0 ELSE 1 END
    """)

    op.execute("DROP INDEX IF EXISTS ix_tasks_search")
    for column in (
        "subagent_id",
        "execution_agent_instance_id",
        "execution_parent_session_id",
        "execution_turn_id",
        "last_turn_at",
        "brief",
    ):
        op.drop_column("tasks", column)
    op.execute(
        "CREATE INDEX ix_tasks_search ON tasks USING bm25 "
        "(id, title, conclusion, room_id) WITH (key_field = 'id', "
        f"text_fields = '{_text_fields(('title', 'conclusion'), ('room_id',))}')"
    )

    # A teammate's proposals: a table of their own, decided once.
    op.create_table(
        "task_proposals",
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
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("proposed_by", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="open"),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_proposals_project_id", "task_proposals", ["project_id"])
    op.create_index("ix_task_proposals_room_id", "task_proposals", ["room_id"])


def downgrade() -> None:
    raise NotImplementedError("A task's brief became its document; no way back.")
