"""A 支线 becomes a task in place

Revision ID: e4f7009313c1
Revises: 3f7a9c2e1d04
Create Date: 2026-10-15

A 支线 that has become one piece of work turns into a task where it is: the
task takes the 支线's id, so the conversation, its messages, its AI teammate's
session and what it has spent stay where they are. The ``threads`` row stays
too — it is what hangs the conversation under its message in the main line.

The register (``conversations``) holds one row per id, so the triggers learn
that one id can be both:

- **inserting a task** whose id is a 支线's turns that 支线's row into a task's
  instead of registering a second one;
- **deleting a task** whose 支线 is still there turns the row back into the
  支线's, and **deleting a 支线** that is a task leaves the row alone. Deleting
  the row deletes everything said in the conversation and its sessions, so
  only the last of the two to go takes it with it.
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "e4f7009313c1"
down_revision: str | Sequence[str] | None = "3f7a9c2e1d04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("conversations, tasks, threads")
    op.execute("""
        CREATE OR REPLACE FUNCTION conversation_registered() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_ARGV[0] = 'task'
               AND EXISTS (SELECT 1 FROM threads WHERE id = NEW.id) THEN
                UPDATE conversations SET kind = 'task' WHERE id = NEW.id;
            ELSE
                INSERT INTO conversations (id, project_id, kind)
                VALUES (NEW.id, NEW.project_id, TG_ARGV[0]);
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION conversation_unregistered() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_ARGV[0] = 'task'
               AND EXISTS (SELECT 1 FROM threads WHERE id = OLD.id) THEN
                UPDATE conversations SET kind = 'thread' WHERE id = OLD.id;
            ELSE
                DELETE FROM conversations
                WHERE id = OLD.id AND kind = TG_ARGV[0];
            END IF;
            RETURN OLD;
        END;
        $$
    """)
    for table, kind in (("topics", "room"), ("tasks", "task"), ("threads", "thread")):
        op.execute(f"DROP TRIGGER {table}_conversation_unregistered ON {table}")
        op.execute(f"""
            CREATE TRIGGER {table}_conversation_unregistered
            AFTER DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION conversation_unregistered('{kind}')
        """)


def downgrade() -> None:
    raise NotImplementedError("a task made from a 支线 is not taken back apart")
