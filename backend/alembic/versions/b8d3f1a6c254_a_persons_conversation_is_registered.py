"""A person's 芝士 conversation is a conversation of the register too

Revision ID: b8d3f1a6c254
Revises: b7d3e19a4c52
Create Date: 2026-10-23

A sandbox, the run it is charged as, the session that works in it and what it
spends point at the register of conversations (``conversations``), not at the
table a conversation lives in. A person's own 芝士 conversation
(``assistant_conversations``) now registers there as well, under the new kind
``personal``, so a sandbox can be one of its own:

- **`conversations.project_id`** may be NULL, and is exactly for a personal
  conversation: it is in no project.
- **`assistant_conversations`** registers and unregisters itself as rooms,
  tasks and 支线 do; the ones already there are registered now.
- **`sandbox_homes.project_id`** and **`compute_runs.project_id`** may be NULL:
  a personal conversation's sandbox is in no project, and its run is charged to
  its person.
- **`compute_runs.topic_id`** points at the register instead of ``topics``: a
  run names the conversation its sandbox is for, which is how a personal one
  finds who pays.

The previous release keeps reading all of these while this runs: it reads no
row of the new kind, since every read it makes names a project or a room.
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b8d3f1a6c254"
down_revision: str | Sequence[str] | None = "b7d3e19a4c52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK = "fk_compute_runs_topic_id_conversations"
_INDEX = "ix_compute_runs_topic_id"


def upgrade() -> None:
    with_lock_retries(
        "conversations, assistant_conversations, sandbox_homes, compute_runs"
    )
    op.alter_column("conversations", "project_id", nullable=True)
    op.drop_constraint("ck_conversations_kind", "conversations", type_="check")
    op.create_check_constraint(
        "ck_conversations_kind",
        "conversations",
        "kind IN ('room', 'task', 'thread', 'personal')",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_conversations_project",
        "conversations",
        "(kind = 'personal') = (project_id IS NULL)",
        postgresql_not_valid=True,
    )
    op.execute("""
        CREATE FUNCTION personal_conversation_registered() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO conversations (id, project_id, kind, created_at)
            VALUES (NEW.id, NULL, 'personal', NEW.created_at);
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        INSERT INTO conversations (id, project_id, kind, created_at)
        SELECT id, NULL, 'personal', created_at FROM assistant_conversations
    """)
    op.execute("""
        CREATE TRIGGER assistant_conversations_conversation_registered
        BEFORE INSERT ON assistant_conversations
        FOR EACH ROW EXECUTE FUNCTION personal_conversation_registered()
    """)
    op.execute("""
        CREATE TRIGGER assistant_conversations_conversation_unregistered
        AFTER DELETE ON assistant_conversations
        FOR EACH ROW EXECUTE FUNCTION conversation_unregistered('personal')
    """)

    op.alter_column("sandbox_homes", "project_id", nullable=True)
    op.alter_column("compute_runs", "project_id", nullable=True)
    op.drop_constraint("compute_runs_topic_id_fkey", "compute_runs", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "compute_runs",
        "conversations",
        ["topic_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    with op.get_context().autocommit_block():
        for table, constraint in (
            ("conversations", "ck_conversations_kind"),
            ("conversations", "ck_conversations_project"),
            ("compute_runs", _FK),
        ):
            op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
        op.create_index(
            _INDEX, "compute_runs", ["topic_id"], postgresql_concurrently=True
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            _INDEX,
            table_name="compute_runs",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_constraint(_FK, "compute_runs", type_="foreignkey")
    op.execute(
        "UPDATE compute_runs SET topic_id = NULL "
        "WHERE topic_id NOT IN (SELECT id FROM topics)"
    )
    op.create_foreign_key(
        "compute_runs_topic_id_fkey",
        "compute_runs",
        "topics",
        ["topic_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute("DELETE FROM compute_runs WHERE project_id IS NULL")
    op.alter_column("compute_runs", "project_id", nullable=False)
    op.execute("DELETE FROM sandbox_homes WHERE project_id IS NULL")
    op.alter_column("sandbox_homes", "project_id", nullable=False)
    op.execute(
        "DROP TRIGGER assistant_conversations_conversation_unregistered "
        "ON assistant_conversations"
    )
    op.execute(
        "DROP TRIGGER assistant_conversations_conversation_registered "
        "ON assistant_conversations"
    )
    op.execute("DROP FUNCTION personal_conversation_registered()")
    op.execute("DELETE FROM conversations WHERE kind = 'personal'")
    op.drop_constraint("ck_conversations_project", "conversations", type_="check")
    op.drop_constraint("ck_conversations_kind", "conversations", type_="check")
    op.create_check_constraint(
        "ck_conversations_kind",
        "conversations",
        "kind IN ('room', 'task', 'thread')",
    )
    op.alter_column("conversations", "project_id", nullable=False)
