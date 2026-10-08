"""支线: the replies under a message in a channel are a conversation of their own

- **`threads`** holds one row a 支线: its channel, the message it hangs under
  (one a message), and how many replies it has and when the last came. Its id
  is its conversation's, registered by the same triggers as rooms and tasks
  (`f7985445d2bf`), under the new kind `thread`.
- **`task_proposals.conversation_id`**: the conversation a proposal was made
  in, so its card stays where it was said — a 支线, or (every proposal before
  this) the channel's main line.

Revision ID: 4562fd5e0eeb
Revises: c7e2a91f4d3b
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "4562fd5e0eeb"
down_revision: str | Sequence[str] | None = "c7e2a91f4d3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("conversations, task_proposals, blocks")
    op.drop_constraint("ck_conversations_kind", "conversations", type_="check")
    op.create_check_constraint(
        "ck_conversations_kind",
        "conversations",
        "kind IN ('room', 'task', 'thread')",
    )
    op.create_table(
        "threads",
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
        sa.Column(
            "root_block_id",
            sa.Uuid(),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reply_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_reply_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("root_block_id", name="uq_threads_root_block_id"),
    )
    op.create_index("ix_threads_project_id", "threads", ["project_id"])
    op.create_index("ix_threads_room_id", "threads", ["room_id"])
    op.create_index("ix_threads_last_reply_at", "threads", ["last_reply_at"])
    op.execute("""
        CREATE TRIGGER threads_conversation_registered
        BEFORE INSERT ON threads
        FOR EACH ROW EXECUTE FUNCTION conversation_registered('thread')
    """)
    op.execute("""
        CREATE TRIGGER threads_conversation_unregistered
        AFTER DELETE ON threads
        FOR EACH ROW EXECUTE FUNCTION conversation_unregistered()
    """)

    op.execute("""
        CREATE FUNCTION thread_replied() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE threads
            SET reply_count = reply_count + 1,
                last_reply_at = GREATEST(COALESCE(last_reply_at, NEW.created_at),
                                         NEW.created_at)
            WHERE id = NEW.conversation_id;
            RETURN NULL;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER blocks_thread_replied
        AFTER INSERT ON blocks
        FOR EACH ROW WHEN (NEW.kind = 'message')
        EXECUTE FUNCTION thread_replied()
    """)

    op.add_column(
        "task_proposals", sa.Column("conversation_id", sa.Uuid(), nullable=True)
    )
    op.execute("UPDATE task_proposals SET conversation_id = room_id")
    op.alter_column("task_proposals", "conversation_id", nullable=False)
    op.create_foreign_key(
        "fk_task_proposals_conversation_id",
        "task_proposals",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_task_proposals_conversation_id", "task_proposals", ["conversation_id"]
    )


def downgrade() -> None:
    raise NotImplementedError("支线 are not taken back apart")
